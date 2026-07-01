"""
Matplotlib graph widgets embedded in the Tkinter window.

LiveForceGraph      — Force vs Time line graph, updated during a test.
ComparisonGraph     — Bar chart of peak forces for all completed samples.

Both use FigureCanvasTkAgg so they live inside Tkinter frames and require
no separate matplotlib window.  All update calls must originate from the
Tkinter main thread.

Mac note: canvas.draw() is deferred via after() because on macOS the TkAgg
canvas renders as a black rectangle until the Tk event loop is running.
"""

import collections
import sys
from typing import List

import matplotlib
matplotlib.use("TkAgg")

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import tkinter as tk

_ON_MAC = sys.platform == "darwin"
# Retina-safe DPI — 96 looks good on both normal and HiDPI screens
_DPI = 96


# ── Dark-theme colours ────────────────────────────────────────────────────────
_BG        = "#0d1117"
_PANEL_BG  = "#161b22"
_GRID      = "#21262d"
_TEXT      = "#8b949e"
_LINE      = "#3fb950"    # green  — live force
_PEAK_LINE = "#f85149"    # red    — peak marker
_BAR       = "#1f6feb"    # blue   — comparison bars
_BAR_DONE  = "#238636"    # green  — last bar (most recent)


def _style_axes(ax, title: str, xlabel: str = "", ylabel: str = "") -> None:
    """Apply the shared dark theme to an Axes object."""
    ax.set_facecolor(_BG)
    if title:
        ax.set_title(title, color=_TEXT, fontsize=9, fontweight="bold", pad=4)
    if xlabel:
        ax.set_xlabel(xlabel, color=_TEXT, fontsize=8)
    if ylabel:
        ax.set_ylabel(ylabel, color=_TEXT, fontsize=8)
    ax.tick_params(colors=_TEXT, labelsize=7)
    for spine in ax.spines.values():
        spine.set_edgecolor(_GRID)
    ax.grid(True, color=_GRID, linestyle="--", linewidth=0.5, alpha=0.8)


# ── LiveForceGraph ─────────────────────────────────────────────────────────────

class LiveForceGraph(tk.Frame):
    """
    Continuously updated Force-vs-Time plot.

    Call update(t, force) for each new data point.
    Call reset() before every new sample.
    """

    def __init__(self, parent: tk.Widget, history_length: int = 500, **kwargs) -> None:
        super().__init__(parent, bg=_BG, **kwargs)
        self._maxlen = history_length
        self._times:  collections.deque = collections.deque(maxlen=history_length)
        self._forces: collections.deque = collections.deque(maxlen=history_length)
        self._peak: float = 0.0

        # Figure
        self._fig = Figure(figsize=(5, 2.6), dpi=_DPI, facecolor=_PANEL_BG)
        self._ax = self._fig.add_subplot(111)
        _style_axes(self._ax, "Live Force", xlabel="Time (s)", ylabel="Force (N)")

        self._line, = self._ax.plot(
            [], [], color=_LINE, linewidth=1.8, label="Force (N)", zorder=3)
        self._peak_line = self._ax.axhline(
            0, color=_PEAK_LINE, linewidth=1.2,
            linestyle="--", label="Peak", alpha=0.85, zorder=2)

        self._ax.legend(
            fontsize=7, facecolor=_PANEL_BG,
            edgecolor=_GRID, labelcolor=_TEXT, loc="upper left")

        self._fig.tight_layout(pad=1.2)

        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        widget = self._canvas.get_tk_widget()
        widget.pack(fill=tk.BOTH, expand=True)

        # Defer draw so the Tk event loop is running first (fixes macOS black canvas)
        self.after(100, self._canvas.draw)
        # Also redraw whenever the widget is mapped (shown) — extra Mac safety
        widget.bind("<Map>", lambda _e: self._canvas.draw_idle())

    # ── Public interface ──────────────────────────────────────────────────────

    def update(self, t: float, force: float) -> None:
        """Add one (time, force) data point and redraw."""
        self._times.append(t)
        self._forces.append(force)

        if force > self._peak:
            self._peak = force
            self._peak_line.set_ydata([self._peak, self._peak])

        ts = list(self._times)
        fs = list(self._forces)
        self._line.set_data(ts, fs)

        if len(ts) > 1:
            self._ax.set_xlim(ts[0], ts[-1] + 0.2)
        max_f = max(fs) if fs else 10.0
        self._ax.set_ylim(-0.5, max_f * 1.2 + 1)

        self._canvas.draw_idle()

    def reset(self) -> None:
        """Clear graph for the next sample."""
        self._times.clear()
        self._forces.clear()
        self._peak = 0.0
        self._line.set_data([], [])
        self._peak_line.set_ydata([0, 0])
        self._ax.set_xlim(0, 10)
        self._ax.set_ylim(-0.5, 100)
        self._canvas.draw_idle()

    def set_history_length(self, n: int) -> None:
        existing_t = list(self._times)[-n:]
        existing_f = list(self._forces)[-n:]
        self._maxlen = n
        self._times  = collections.deque(existing_t, maxlen=n)
        self._forces = collections.deque(existing_f, maxlen=n)


# ── ComparisonGraph ───────────────────────────────────────────────────────────

class ComparisonGraph(tk.Frame):
    """
    Bar chart comparing peak forces of every completed sample.
    Rebuilt from scratch on each update() call.
    """

    def __init__(self, parent: tk.Widget, **kwargs) -> None:
        super().__init__(parent, bg=_BG, **kwargs)

        self._fig = Figure(figsize=(5, 2.4), dpi=_DPI, facecolor=_PANEL_BG)
        self._ax = self._fig.add_subplot(111)
        _style_axes(self._ax, "Sample Comparison", ylabel="Peak Force (N)")
        # Remove x-grid (bars already separate values visually)
        self._ax.grid(False, axis="x")

        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        widget = self._canvas.get_tk_widget()
        widget.pack(fill=tk.BOTH, expand=True)

        # Deferred draw — same Mac fix as LiveForceGraph
        self.after(100, self._canvas.draw)
        widget.bind("<Map>", lambda _e: self._canvas.draw_idle())

    def update(self, names: List[str], peaks: List[float]) -> None:
        """Redraw the bar chart with the latest completed sample data."""
        ax = self._ax
        ax.clear()
        _style_axes(ax, "Sample Comparison", ylabel="Peak Force (N)")
        ax.grid(True, color=_GRID, linestyle="--", linewidth=0.5,
                axis="y", alpha=0.8)

        if not names:
            self._canvas.draw_idle()
            return

        # Colour the most recent bar distinctively
        colours = [_BAR] * len(names)
        colours[-1] = _BAR_DONE

        bars = ax.bar(range(len(names)), peaks, color=colours,
                      edgecolor=_GRID, width=0.55, zorder=3)
        ax.set_xticks(range(len(names)))
        ax.set_xticklabels(names, fontsize=7, color=_TEXT, rotation=20, ha="right")

        for bar, peak in zip(bars, peaks):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(peaks) * 0.02,
                f"{peak:.1f}",
                ha="center", va="bottom",
                fontsize=7, color=_TEXT,
            )

        ax.set_ylim(0, max(peaks) * 1.25 + 1)
        self._fig.tight_layout(pad=1.0)
        self._canvas.draw_idle()
