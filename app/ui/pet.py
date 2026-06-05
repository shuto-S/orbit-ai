from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO

PET_STATES = {"idle", "thinking", "speaking", "waiting", "notifying", "error"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PetProcess(Protocol):
    stdin: TextIO | None
    stdout: TextIO | None
    stderr: TextIO | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


PopenFactory = Callable[..., PetProcess]
RunFactory = Callable[..., subprocess.CompletedProcess[str]]
NATIVE_PACKAGE_DIR = PROJECT_ROOT / "tools" / "pet-overlay"
NATIVE_EXECUTABLE = NATIVE_PACKAGE_DIR / ".build" / "release" / "orbit-pet-overlay"


@dataclass(frozen=True)
class PetUIStatus:
    enabled: bool
    running: bool
    fallback: bool
    reason: str


class PetUI:
    def __init__(
        self,
        config: dict[str, Any],
        popen_factory: PopenFactory | None = None,
        build_runner: RunFactory | None = None,
        interactive: bool = True,
        python_executable: str | None = None,
    ) -> None:
        self.config = config
        self.popen_factory = popen_factory or subprocess.Popen
        self.build_runner = build_runner or subprocess.run
        self.interactive = interactive
        self.python_executable = python_executable or sys.executable
        self.process: PetProcess | None = None
        self.disabled_reason = ""
        self.fallback = False
        self._submitted_text: queue.Queue[str] = queue.Queue()

    @property
    def show_progress_enabled(self) -> bool:
        return bool(self.config.get("show_progress", True))

    def start(self) -> bool:
        if self.process is not None and self.process.poll() is None:
            return True
        reason = self._disabled_reason()
        if reason:
            self.disabled_reason = reason
            return False
        command = self._overlay_command()
        if command is None:
            return False
        try:
            process = self.popen_factory(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(PROJECT_ROOT),
                env=self._overlay_env(),
            )
        except OSError as exc:
            self._disable_with_fallback(f"overlay start failed: {command[0]}: {exc}")
            return False
        self.process = process
        time.sleep(0.25)
        if process.poll() is not None:
            self._disable_with_fallback(f"overlay start failed: {command[0]}: {_startup_error(process)}")
            return False
        self._start_output_reader(process)
        self.set_state("idle")
        return True

    def say(self, text: str, state: str = "speaking") -> bool:
        return self._send({"type": "say", "text": text, "state": _safe_state(state, "speaking")})

    def progress(self, text: str) -> bool:
        if not self.show_progress_enabled:
            return False
        return self._send({"type": "progress", "text": text})

    def set_state(self, state: str, text: str | None = None) -> bool:
        payload: dict[str, Any] = {"type": "state", "state": _safe_state(state, "idle")}
        if text is not None:
            payload["text"] = text
        return self._send(payload)

    def hide(self) -> bool:
        return self._send({"type": "hide"})

    def show(self) -> bool:
        return self._send({"type": "show"})

    def pop_submitted_text(self) -> str | None:
        try:
            return self._submitted_text.get_nowait()
        except queue.Empty:
            return None

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        if process.stdin is not None and process.poll() is None:
            try:
                process.stdin.write(json.dumps({"type": "quit"}) + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except OSError:
                pass

    def status(self) -> PetUIStatus:
        if self.process is not None and self.process.poll() is not None and not self.disabled_reason:
            self._disable_with_fallback(f"overlay exited: {_startup_error(self.process)}")
        running = self.process is not None and self.process.poll() is None
        current_disabled_reason = self._disabled_reason()
        enabled = not current_disabled_reason
        fallback = self.fallback or (enabled and not running)
        reason = self.disabled_reason or current_disabled_reason
        if not reason and running:
            reason = "running"
        if not reason and fallback:
            reason = "overlay unavailable"
        if not reason:
            reason = "disabled"
        return PetUIStatus(enabled=enabled, running=running, fallback=fallback, reason=reason)

    def _send(self, payload: dict[str, Any]) -> bool:
        if not self.start():
            return False
        if self.process is None or self.process.stdin is None or self.process.poll() is not None:
            self._disable_with_fallback("overlay process is not running")
            return False
        try:
            self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._disable_with_fallback(f"overlay pipe failed: {exc}")
            return False
        return True

    def _disabled_reason(self) -> str:
        if _env_disabled():
            return "disabled by ORBIT_AI_PET"
        if not self.config.get("enabled", True):
            return "disabled by config"
        if str(self.config.get("mode", "desktop_overlay")) != "desktop_overlay":
            return "unsupported pet mode"
        if not self.interactive:
            return "disabled in non-interactive runtime"
        return ""

    def _disable_with_fallback(self, reason: str) -> None:
        self.disabled_reason = reason
        self.fallback = bool(self.config.get("fallback_to_terminal", True))
        self.process = None

    def _overlay_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["ORBIT_AI_PET_POSITION"] = str(self.config.get("position", "bottom_right"))
        env["ORBIT_AI_PET_ALWAYS_ON_TOP"] = "1" if self.config.get("always_on_top", True) else "0"
        pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(PROJECT_ROOT) if not pythonpath else f"{PROJECT_ROOT}{os.pathsep}{pythonpath}"
        return env

    def _python_executable(self) -> str:
        configured = self.config.get("python_executable") or os.environ.get("ORBIT_AI_PET_PYTHON")
        return str(configured or self.python_executable)

    def _overlay_command(self) -> list[str] | None:
        backend = str(self.config.get("backend", "native"))
        if backend in {"native", "swift", "swift_appkit"}:
            return self._native_overlay_command()
        if backend in {"python_tk", "tk"}:
            return [self._python_executable(), "-m", "app.ui.pet_overlay"]
        self._disable_with_fallback(f"unsupported pet backend: {backend}")
        return None

    def _native_overlay_command(self) -> list[str] | None:
        executable = self._native_executable_path()
        if executable.exists():
            return [str(executable)]
        if not self.config.get("auto_build", True):
            self._disable_with_fallback(f"native overlay executable not found: {executable}")
            return None
        if self._build_native_overlay(executable):
            return [str(executable)]
        return None

    def _native_executable_path(self) -> Path:
        configured = self.config.get("native_executable") or os.environ.get("ORBIT_AI_PET_NATIVE_EXECUTABLE")
        return Path(str(configured)) if configured else NATIVE_EXECUTABLE

    def _build_native_overlay(self, executable: Path) -> bool:
        swift_executable = str(self.config.get("swift_executable", "swift"))
        timeout = float(self.config.get("build_timeout_seconds", 30))
        try:
            result = self.build_runner(
                [
                    swift_executable,
                    "build",
                    "-c",
                    "release",
                    "--package-path",
                    str(NATIVE_PACKAGE_DIR),
                ],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            self._disable_with_fallback("native overlay build timed out")
            return False
        except OSError as exc:
            self._disable_with_fallback(f"native overlay build failed: {exc}")
            return False
        if result.returncode != 0:
            self._disable_with_fallback(f"native overlay build failed: {_summarize_process_output(result)}")
            return False
        if not executable.exists():
            self._disable_with_fallback(f"native overlay build did not produce executable: {executable}")
            return False
        return True

    def _start_output_reader(self, process: PetProcess) -> None:
        if process.stdout is None:
            return

        def read_events() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict) or payload.get("type") != "submit":
                    continue
                text = str(payload.get("text") or "").strip()
                if text:
                    self._submitted_text.put(text)

        threading.Thread(target=read_events, daemon=True).start()


def _safe_state(value: str, default: str) -> str:
    return value if value in PET_STATES else default


def _env_disabled() -> bool:
    return os.environ.get("ORBIT_AI_PET", "").strip().lower() in {"0", "false", "off", "no"}


def _startup_error(process: PetProcess) -> str:
    stderr = process.stderr.read().strip() if process.stderr is not None else ""
    if stderr:
        return _summarize_startup_error(stderr)
    return "overlay process exited during startup"


def _summarize_process_output(result: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part for part in [result.stderr, result.stdout] if part)
    return _summarize_startup_error(output) if output else f"exit code {result.returncode}"


def _summarize_startup_error(stderr: str) -> str:
    compact = " ".join(stderr.split())
    if "Can't find a usable init.tcl" in stderr:
        return "tkinter Tcl/Tk runtime is unavailable"
    if "version conflict for package" in stderr:
        return "tkinter Tcl/Tk version mismatch"
    if "No module named '_tkinter'" in stderr:
        return "python was built without _tkinter"
    if "macOS" in stderr and "required" in stderr:
        return "system tkinter is incompatible with this macOS runtime"
    if "Tcl_Panic" in stderr or "abort() called" in stderr:
        return "tkinter aborted during startup"
    if "Traceback" in stderr:
        last_line = _last_meaningful_line(stderr)
        return last_line[:120] if last_line else "overlay process exited during startup"
    return compact[:120]


def _last_meaningful_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped and not stripped.startswith("File ") and not stripped.startswith("Traceback"):
            return stripped
    return ""
