"""
Reusable custom Tkinter widgets for LoadCellTester.
"""
import tkinter as tk
from tkinter import ttk
from typing import Optional


# ── Shared Colour Palette ─────────────────────────────────────────────────────
COLOURS = {
    "bg":           "#0d1117",
    "bg_panel":     "#161b22",
    "bg_section":   "#1c2128",
    "border":       "#30363d",
    "accent":       "#238636",
    "accent_blue":  "#1f6feb",
    "text":         "#e6edf3",
    "text_muted":   "#8b949e",
    "success":      "#3fb950",
    "warning":      "#d29922",
    "danger":       "#f85149",
    "info":         "#388bfd",
}

_LAMP_COLOURS = {
    "green":  "#3fb950",
    "red":    "#f85149",
    "yellow": "#d29922",
    "blue":   "#388bfd",
    "grey":   "#21262d",
    "orange": "#e3b341",
}


# ── StatusLamp ────────────────────────────────────────────────────────────────

class StatusLamp(tk.Canvas):
    """A small circular colour indicator."""

    def __init__(self, parent: tk.Widget, size: int = 14, **kwargs) -> None:
        bg = kwargs.pop("bg", COLOURS["bg_panel"])
        super().__init__(parent, width=size, height=size,
                         bg=bg, highlightthickness=0, **kwargs)
        pad = max(2, size // 6)
        self._oval = self.create_oval(
            pad, pad, size - pad, size - pad,
            fill=_LAMP_COLOURS["grey"],
            outline="#444",
            width=1,
        )

    def set_colour(self, colour: str) -> None:
        self.itemconfig(self._oval, fill=_LAMP_COLOURS.get(colour, _LAMP_COLOURS["grey"]))

    def set_state(self, active: bool, active_colour: str = "green") -> None:
        self.set_colour(active_colour if active else "grey")


# ── BigValueDisplay ───────────────────────────────────────────────────────────

class BigValueDisplay(tk.Frame):
    """Large numeric readout with a header label and unit footer."""

    def __init__(
        self,
        parent: tk.Widget,
        label: str,
        unit: str = "N",
        colour: str = "#3fb950",
        **kwargs,
    ) -> None:
        super().__init__(parent, bg=COLOURS["bg_section"], relief=tk.FLAT, **kwargs)

        tk.Label(
            self, text=label,
            font=("Consolas", 8, "bold"),
            fg=COLOURS["text_muted"],
            bg=COLOURS["bg_section"],
        ).pack(pady=(6, 0))

        self._var = tk.StringVar(value="—")
        tk.Label(
            self, textvariable=self._var,
            font=("Consolas", 26, "bold"),
            fg=colour,
            bg=COLOURS["bg_section"],
        ).pack()

        tk.Label(
            self, text=unit,
            font=("Consolas", 9),
            fg=COLOURS["text_muted"],
            bg=COLOURS["bg_section"],
        ).pack(pady=(0, 6))

    def set_value(self, value: Optional[float]) -> None:
        if value is None:
            self._var.set("—")
        else:
            self._var.set(f"{value:,.3f}")


# ── SectionFrame ──────────────────────────────────────────────────────────────

class SectionFrame(tk.LabelFrame):
    """Styled LabelFrame used as a grouping container throughout the UI."""

    def __init__(self, parent: tk.Widget, title: str, **kwargs) -> None:
        bg = kwargs.pop("bg", COLOURS["bg_panel"])
        super().__init__(
            parent,
            text=f"  {title}  ",
            font=("Segoe UI", 8, "bold"),
            fg=COLOURS["text_muted"],
            bg=bg,
            relief=tk.GROOVE,
            bd=1,
            **kwargs,
        )
        self.configure(bg=bg)
