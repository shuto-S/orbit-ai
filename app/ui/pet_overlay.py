from __future__ import annotations

import json
import os
import queue
import sys
import textwrap
import threading
from dataclasses import dataclass
from typing import Any

WIDTH = 320
HEIGHT = 190
MARGIN = 24

STATE_COLORS = {
    "idle": ("#f2f6ff", "#4f6bed"),
    "thinking": ("#fff7df", "#c47f00"),
    "speaking": ("#ecfff3", "#198754"),
    "waiting": ("#f4f4f5", "#52525b"),
    "notifying": ("#fff0f3", "#d6336c"),
    "error": ("#fff1f2", "#dc2626"),
}


@dataclass
class PetState:
    visible: bool = True
    state: str = "idle"
    text: str = "待機しています。"
    pulse: int = 0


class PetOverlay:
    def __init__(self) -> None:
        import tkinter as tk

        self.tk = tk
        self.root = tk.Tk()
        self.root.title("Orbit Pet")
        self.root.overrideredirect(True)
        self.root.resizable(False, False)
        if os.environ.get("ORBIT_AI_PET_ALWAYS_ON_TOP", "1") != "0":
            self.root.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT, highlightthickness=0, bg="#ffffff")
        self.canvas.pack()
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.state = PetState()
        self._place_window()
        self._draw()
        self._start_stdin_reader()
        self.root.after(100, self._poll)
        self.root.after(500, self._animate)

    def run(self) -> None:
        self.root.mainloop()

    def _place_window(self) -> None:
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        position = os.environ.get("ORBIT_AI_PET_POSITION", "bottom_right")
        if position == "bottom_left":
            x = MARGIN
            y = screen_height - HEIGHT - MARGIN
        elif position == "top_right":
            x = screen_width - WIDTH - MARGIN
            y = MARGIN
        elif position == "top_left":
            x = MARGIN
            y = MARGIN
        else:
            x = screen_width - WIDTH - MARGIN
            y = screen_height - HEIGHT - MARGIN
        self.root.geometry(f"{WIDTH}x{HEIGHT}+{max(0, x)}+{max(0, y)}")

    def _start_stdin_reader(self) -> None:
        def worker() -> None:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    self.queue.put(payload)

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        while True:
            try:
                payload = self.queue.get_nowait()
            except queue.Empty:
                break
            self._handle(payload)
        self.root.after(100, self._poll)

    def _animate(self) -> None:
        self.state.pulse = (self.state.pulse + 1) % 4
        if self.state.visible:
            self._draw()
        self.root.after(500, self._animate)

    def _handle(self, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("type", ""))
        if event_type == "quit":
            self.root.destroy()
            return
        if event_type == "hide":
            self.state.visible = False
            self.root.withdraw()
            return
        if event_type == "show":
            self.state.visible = True
            self.root.deiconify()
            self._draw()
            return
        if event_type == "say":
            self.state.state = _state(payload.get("state"), "speaking")
            self.state.text = _clean_text(payload.get("text"), "話しています。")
            self.state.visible = True
            self.root.deiconify()
            self._draw()
            return
        if event_type == "progress":
            self.state.state = "thinking"
            self.state.text = _clean_text(payload.get("text"), "進行中です。")
            self.state.visible = True
            self.root.deiconify()
            self._draw()
            return
        if event_type == "state":
            self.state.state = _state(payload.get("state"), "idle")
            if "text" in payload:
                self.state.text = _clean_text(payload.get("text"), self.state.text)
            self._draw()

    def _draw(self) -> None:
        bg, accent = STATE_COLORS.get(self.state.state, STATE_COLORS["idle"])
        self.canvas.delete("all")
        self.canvas.create_rectangle(0, 0, WIDTH, HEIGHT, fill=bg, outline=bg)
        self._draw_bubble(accent)
        self._draw_character(accent)

    def _draw_bubble(self, accent: str) -> None:
        x0, y0, x1, y1 = 18, 14, 300, 86
        self.canvas.create_rectangle(x0, y0, x1, y1, fill="#ffffff", outline=accent, width=2)
        self.canvas.create_polygon(76, y1, 92, y1, 82, y1 + 14, fill="#ffffff", outline=accent)
        lines = textwrap.wrap(self.state.text, width=24, max_lines=3, placeholder="...")
        self.canvas.create_text(
            30,
            28,
            text="\n".join(lines),
            anchor="nw",
            fill="#1f2937",
            font=("Helvetica", 13),
        )

    def _draw_character(self, accent: str) -> None:
        cx, cy = 116, 133 + (1 if self.state.pulse % 2 == 0 else -1)
        self.canvas.create_oval(cx - 44, cy - 42, cx + 44, cy + 42, fill="#ffffff", outline=accent, width=3)
        self.canvas.create_oval(cx - 22, cy - 12, cx - 10, cy, fill=accent, outline=accent)
        self.canvas.create_oval(cx + 10, cy - 12, cx + 22, cy, fill=accent, outline=accent)
        if self.state.state == "thinking":
            mouth = "..."
        elif self.state.state == "notifying":
            mouth = "!"
        elif self.state.state == "error":
            mouth = "x"
        else:
            mouth = "u"
        self.canvas.create_text(cx, cy + 20, text=mouth, fill=accent, font=("Helvetica", 18, "bold"))
        self.canvas.create_arc(cx - 58, cy - 58, cx - 26, cy - 16, start=20, extent=120, outline=accent, width=3)
        self.canvas.create_arc(cx + 26, cy - 58, cx + 58, cy - 16, start=60, extent=120, outline=accent, width=3)
        self.canvas.create_text(230, 142, text=self.state.state, fill=accent, font=("Helvetica", 12, "bold"))


def _clean_text(value: object, default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text or default


def _state(value: object, default: str) -> str:
    text = str(value).strip()
    if text in STATE_COLORS:
        return text
    return default


def main() -> None:
    PetOverlay().run()


if __name__ == "__main__":
    main()
