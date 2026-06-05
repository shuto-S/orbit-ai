from __future__ import annotations

import io
import subprocess
import time
from typing import Any

from app.cli.commands import handle_pet_command
from app.main import _autonomous_output
from app.ui.pet import NATIVE_PACKAGE_DIR as PET_PACKAGE_DIR
from app.ui.pet import PetUI, _summarize_startup_error


class FakeProcess:
    def __init__(self, exit_code: int | None = None, stderr: str = "", stdout: str = "") -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.exit_code = exit_code
        self.terminated = False
        self.waited = False

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True
        self.exit_code = 0

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return self.exit_code or 0


class FakePet:
    def __init__(self) -> None:
        self.say_calls: list[tuple[str, str]] = []

    def say(self, text: str, state: str = "speaking") -> bool:
        self.say_calls.append((text, state))
        return True


def test_pet_ui_disabled_config_does_not_start_subprocess() -> None:
    calls: list[Any] = []
    pet = PetUI(
        {"enabled": False},
        popen_factory=lambda *args, **kwargs: calls.append((args, kwargs)),
        interactive=True,
    )

    assert not pet.start()

    assert calls == []
    assert pet.status().running is False
    assert pet.status().reason == "disabled by config"


def test_pet_ui_start_failure_falls_back_without_raising(tmp_path: Any) -> None:
    executable = tmp_path / "orbit-pet-overlay"
    executable.write_text("")

    def fail_start(*_args: Any, **_kwargs: Any) -> FakeProcess:
        raise OSError("no display")

    pet = PetUI(
        {"enabled": True, "fallback_to_terminal": True, "native_executable": str(executable)},
        popen_factory=fail_start,
        interactive=True,
    )

    assert not pet.start()

    status = pet.status()
    assert status.running is False
    assert status.fallback is True
    assert "no display" in status.reason


def test_pet_ui_builds_native_overlay_when_missing(tmp_path: Any) -> None:
    executable = tmp_path / "orbit-pet-overlay"
    build_calls: list[list[str]] = []
    start_calls: list[list[str]] = []

    def build(args: list[str], *_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        build_calls.append(args)
        executable.write_text("")
        return subprocess.CompletedProcess(args, 0, "", "")

    def start(args: list[str], *_args: Any, **_kwargs: Any) -> FakeProcess:
        start_calls.append(args)
        return FakeProcess()

    pet = PetUI({"enabled": True, "native_executable": str(executable)}, popen_factory=start, build_runner=build)

    assert pet.start()

    assert build_calls == [["swift", "build", "-c", "release", "--package-path", str(PET_PACKAGE_DIR)]]
    assert start_calls == [[str(executable)]]


def test_pet_ui_native_build_failure_falls_back(tmp_path: Any) -> None:
    executable = tmp_path / "orbit-pet-overlay"

    def build(args: list[str], *_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "swift error")

    pet = PetUI({"enabled": True, "native_executable": str(executable)}, build_runner=build)

    assert not pet.start()

    status = pet.status()
    assert status.running is False
    assert status.fallback is True
    assert "native overlay build failed" in status.reason


def test_pet_ui_summarizes_tk_runtime_traceback() -> None:
    stderr = """
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "/Users/shuto/src/private/orbit-ai/app/ui/pet_overlay.py", line 198, in <module>
    main()
_tkinter.TclError: Can't find a usable init.tcl in the following directories:
    /tools/deps/lib/tcl8.6
This probably means that Tcl wasn't installed properly.
"""

    assert _summarize_startup_error(stderr) == "tkinter Tcl/Tk runtime is unavailable"


def test_pet_ui_summarizes_tk_abort_report() -> None:
    stderr = """
Process: Python
Application Specific Information:
abort() called
Thread 0 Crashed:
3 Tcl Tcl_PanicVA
"""

    assert _summarize_startup_error(stderr) == "tkinter aborted during startup"


def test_pet_ui_does_not_try_system_python_when_primary_exits(monkeypatch: Any) -> None:
    calls: list[str] = []

    def start(args: list[str], *_args: Any, **_kwargs: Any) -> FakeProcess:
        calls.append(args[0])
        return FakeProcess(exit_code=1, stderr="tk failed")

    monkeypatch.setattr("app.ui.pet.sys.platform", "darwin")
    pet = PetUI(
        {"enabled": True, "backend": "python_tk"},
        popen_factory=start,
        interactive=True,
        python_executable="/bad/python",
    )

    assert not pet.start()

    assert calls == ["/bad/python"]
    assert pet.status().running is False
    assert "tk failed" in pet.status().reason


def test_pet_ui_uses_configured_python_executable() -> None:
    calls: list[str] = []

    def start(args: list[str], *_args: Any, **_kwargs: Any) -> FakeProcess:
        calls.append(args[0])
        return FakeProcess()

    pet = PetUI(
        {"enabled": True, "backend": "python_tk", "python_executable": "/safe/python"},
        popen_factory=start,
        interactive=True,
    )

    assert pet.start()

    assert calls == ["/safe/python"]
    assert pet.status().running is True


def test_pet_ui_status_reports_late_overlay_exit_reason() -> None:
    pet = PetUI({"enabled": True}, interactive=True)
    pet.process = FakeProcess(exit_code=1, stderr="late tk crash")

    status = pet.status()

    assert status.running is False
    assert status.fallback is True
    assert "late tk crash" in status.reason


def test_pet_ui_sends_jsonl_commands(tmp_path: Any) -> None:
    executable = tmp_path / "orbit-pet-overlay"
    executable.write_text("")
    process = FakeProcess()
    pet = PetUI(
        {"enabled": True, "native_executable": str(executable)},
        popen_factory=lambda *_args, **_kwargs: process,
        interactive=True,
    )

    assert pet.say("確認しますね。", state="thinking")
    assert pet.progress("関連する記憶を検索しています...")
    assert pet.hide()
    assert pet.show()
    pet.stop()

    output = process.stdin.getvalue()
    assert '{"type": "state", "state": "idle"}' in output
    assert '{"type": "say", "text": "確認しますね。", "state": "thinking"}' in output
    assert '{"type": "progress", "text": "関連する記憶を検索しています..."}' in output
    assert '{"type": "hide"}' in output
    assert '{"type": "show"}' in output
    assert '{"type": "quit"}' in output
    assert process.terminated


def test_pet_ui_reads_submitted_text_from_overlay_stdout(tmp_path: Any) -> None:
    executable = tmp_path / "orbit-pet-overlay"
    executable.write_text("")
    process = FakeProcess(stdout='{"type":"submit","text":"今日の予定は？"}\n')
    pet = PetUI(
        {"enabled": True, "native_executable": str(executable)},
        popen_factory=lambda *_args, **_kwargs: process,
        interactive=True,
    )

    assert pet.start()

    deadline = time.monotonic() + 1
    submitted = None
    while time.monotonic() < deadline:
        submitted = pet.pop_submitted_text()
        if submitted is not None:
            break
        time.sleep(0.01)

    assert submitted == "今日の予定は？"


def test_pet_commands_report_status_hide_and_show(capsys: Any, tmp_path: Any) -> None:
    executable = tmp_path / "orbit-pet-overlay"
    executable.write_text("")
    process = FakeProcess()
    pet = PetUI(
        {"enabled": True, "native_executable": str(executable)},
        popen_factory=lambda *_args, **_kwargs: process,
        interactive=True,
    )

    handle_pet_command(pet, "/pet status")
    handle_pet_command(pet, "/pet hide")
    handle_pet_command(pet, "/pet show")
    handle_pet_command(pet, "/pet bad")

    output = capsys.readouterr().out
    assert "Pet UI enabled=True running=False fallback=True" in output
    assert "Pet UI hidden." in output
    assert "Pet UI shown." in output
    assert "Usage: /pet status, /pet hide, or /pet show" in output


def test_autonomous_output_prints_and_notifies_pet(capsys: Any) -> None:
    pet = FakePet()

    _autonomous_output(pet)("リマインドです。水を飲む")  # type: ignore[arg-type]

    assert "AI: リマインドです。水を飲む" in capsys.readouterr().out
    assert pet.say_calls == [("リマインドです。水を飲む", "notifying")]
