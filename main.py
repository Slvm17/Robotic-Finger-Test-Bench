"""
LoadCellTester — application entry point.

Run with:
    python main.py

Package with PyInstaller:
    pyinstaller --onefile --windowed --name LoadCellTester main.py
"""

import os
import sys

# ── Path resolution for both normal execution and PyInstaller onefile mode ─────
if getattr(sys, "frozen", False):
    # PyInstaller bundles everything into a temp directory stored in sys._MEIPASS
    BASE_DIR = sys._MEIPASS  # type: ignore[attr-defined]
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)

import tkinter as tk

from app.config_manager import ConfigManager
from app.dashboard import Dashboard


def main() -> None:
    config = ConfigManager()

    root = tk.Tk()
    root.title("LoadCellTester")
    root.geometry("1440x900")
    root.minsize(1100, 720)

    # Optional taskbar / window icon (won't crash if the file is absent)
    icon_path = os.path.join(BASE_DIR, "assets", "icon.ico")
    if os.path.isfile(icon_path):
        try:
            root.iconbitmap(icon_path)
        except Exception:
            pass

    Dashboard(root, config)
    root.update()          # Force Tk geometry engine to resolve layout before mainloop
    root.mainloop()


if __name__ == "__main__":
    main()
