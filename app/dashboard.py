"""
Main dashboard window for LoadCellTester.

Layout (grid, 3 columns)
────────────────────────────────────────────────────────────────────────
 HEADER  │  App title ▪ Test ID ▪ Operator ▪ Clock
─────────┼────────────────────────┬───────────────────────────────────
 LEFT    │  CENTER                │  RIGHT
 ─────── │  ────────────────────  │  ─────────────────────────────────
 Serial  │  Live Force Graph      │  Current Force  │  Peak Force
 Motor   │  ────────────────────  │  Status lamps
 Notes   │  Sample Table          │  Progress bar
 Config  │  Run / Abort           │  Comparison Graph
         │                        │  Export buttons
────────────────────────────────────────────────────────────────────────
"""

import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import uuid
from datetime import datetime
from typing import Optional

from app.config_manager import ConfigManager
from app.serial_comm import SerialComm, list_ports, SIMULATION_PORT
from app.sample_manager import SampleManager
from app.data_logger import DataLogger
from app.test_sequence import TestSequence
from app.graphs import LiveForceGraph, ComparisonGraph
from app.widgets import StatusLamp, BigValueDisplay, SectionFrame, COLOURS


# How often (ms) we drain the serial RX queue on the main thread
_POLL_MS = 50


class Dashboard:
    """Root-level controller: builds the UI, wires all components together."""

    def __init__(self, root: tk.Tk, config: ConfigManager) -> None:
        self.root   = root
        self.config = config

        # ── Domain objects ──────────────────────────────────────────────────
        self._comm    = SerialComm()
        self._samples = SampleManager()
        self._logger  = DataLogger(config.get("export_folder", "./exports"))
        self._test_id = self._new_test_id()

        self._selected_idx: Optional[int] = None
        self._limit_active = False

        # ── Build UI (order matters — graphs created in _build_center/_build_right)
        self._apply_styles()
        self._build_header()
        self._build_main()

        # ── Wire serial callbacks (must happen after UI exists) ─────────────
        self._comm.register_callback("READY",  self._cb_ready)
        self._comm.register_callback("LIMIT1", self._cb_limit1)
        self._comm.register_callback("LIMIT2", self._cb_limit2)
        self._comm.register_callback("ERROR",  self._cb_error)

        # ── Test sequence (registers its own FORCE/PEAK/DONE/LIMIT callbacks)
        self._sequence = TestSequence(
            serial_comm      = self._comm,
            sample_manager   = self._samples,
            data_logger      = self._logger,
            live_graph       = self._live_graph,
            comparison_graph = self._comparison_graph,
            on_progress      = self._on_progress,
            on_force_update  = self._on_force_update,
            on_complete      = self._on_sample_complete,
            on_error         = self._on_error,
        )

        # ── Start background services ────────────────────────────────────────
        self._tick_clock()
        self._poll_serial()
        # Force graph redraw after the window is fully visible (Mac fix)
        self.root.after(300, self._force_graph_redraw)

    # ═════════════════════════════════════════════════════════════════════════
    # Tkinter style configuration
    # ═════════════════════════════════════════════════════════════════════════

    def _apply_styles(self) -> None:
        C = COLOURS
        s = ttk.Style(self.root)

        # On macOS, 'clam' ignores fieldbackground on Entry/Combobox;
        # 'default' gives us the most cross-platform control.
        # On Windows/Linux, 'clam' looks better.
        _theme = "default" if sys.platform == "darwin" else "clam"
        s.theme_use(_theme)
        self.root.configure(bg=C["bg"])

        # Cross-platform font fallback
        _font = "Helvetica" if sys.platform == "darwin" else "Segoe UI"

        # Baseline
        s.configure(".",
                    background=C["bg"], foreground=C["text"],
                    font=(_font, 9))

        # Frames & labels
        s.configure("TFrame",       background=C["bg"])
        s.configure("TLabel",       background=C["bg"],       foreground=C["text"])
        s.configure("Panel.TFrame", background=C["bg_panel"])

        # LabelFrame
        s.configure("TLabelframe",       background=C["bg_panel"], relief="groove")
        s.configure("TLabelframe.Label", background=C["bg_panel"],
                    foreground=C["text_muted"], font=(_font, 8, "bold"))

        # Buttons
        s.configure("TButton",
                    background=C["bg_section"], foreground=C["text"],
                    relief="flat", padding=(6, 4))
        s.map("TButton",
              background=[("active", C["border"]), ("pressed", C["bg"])])

        s.configure("Accent.TButton",
                    background=C["accent"], foreground="#ffffff",
                    font=(_font, 9, "bold"), relief="flat", padding=(6, 4))
        s.map("Accent.TButton",
              background=[("active", "#2ea043"), ("pressed", "#238636"),
                          ("disabled", "#1c4a27")])

        s.configure("Danger.TButton",
                    background="#b91c1c", foreground="#ffffff",
                    font=(_font, 9, "bold"), relief="flat", padding=(6, 4))
        s.map("Danger.TButton",
              background=[("active", "#ef4444"), ("pressed", "#991b1b")])

        s.configure("Warning.TButton",
                    background=C["warning"], foreground="#000000",
                    font=(_font, 9, "bold"), relief="flat", padding=(6, 4))
        s.map("Warning.TButton",
              background=[("active", "#fbbf24"), ("pressed", "#b45309")])

        # Entry / Combobox / Spinbox
        s.configure("TCombobox",
                    fieldbackground=C["bg_section"], foreground=C["text"],
                    background=C["bg_section"], selectbackground=C["accent_blue"])
        s.configure("TEntry",
                    fieldbackground=C["bg_section"], foreground=C["text"],
                    insertcolor=C["text"])
        s.configure("TSpinbox",
                    fieldbackground=C["bg_section"], foreground=C["text"],
                    background=C["bg_section"])

        # Progressbar
        s.configure("Horizontal.TProgressbar",
                    troughcolor=C["bg_section"],
                    background=C["accent"],
                    thickness=12)

        # Treeview
        s.configure("Treeview",
                    background=C["bg_section"], foreground=C["text"],
                    fieldbackground=C["bg_section"], rowheight=26)
        s.configure("Treeview.Heading",
                    background=C["bg_panel"], foreground=C["text_muted"],
                    font=(_font, 8, "bold"))

        s.map("Treeview",
              background=[("selected", C["accent_blue"])],
              foreground=[("selected", "#ffffff")])

    # ═════════════════════════════════════════════════════════════════════════
    # Header bar
    # ═════════════════════════════════════════════════════════════════════════

    def _build_header(self) -> None:
        C = COLOURS
        hdr = tk.Frame(self.root, bg="#090d13")
        hdr.pack(fill=tk.X, pady=(0, 2))

        # Logo / title
        tk.Label(
            hdr, text="⚙  LoadCellTester",
            font=("Segoe UI", 15, "bold"),
            fg=C["success"], bg="#090d13",
        ).pack(side=tk.LEFT, padx=18)

        # Separator line
        tk.Frame(hdr, bg=C["border"], width=2).pack(
            side=tk.LEFT, fill=tk.Y, pady=8, padx=4)

        # ── Right cluster ────────────────────────────────────────────────────
        right = tk.Frame(hdr, bg="#090d13")
        right.pack(side=tk.RIGHT, padx=14, fill=tk.Y)

        # Clock
        self._clock_var = tk.StringVar()
        tk.Label(right, textvariable=self._clock_var,
                 font=("Consolas", 10), fg=C["text_muted"],
                 bg="#090d13").pack(side=tk.RIGHT, padx=(12, 0))

        # Test ID
        tk.Label(right, text="Test ID:", font=("Segoe UI", 8),
                 fg=C["text_muted"], bg="#090d13").pack(side=tk.RIGHT, padx=(14, 2))
        self._test_id_var = tk.StringVar(value=self._test_id)
        tk.Label(right, textvariable=self._test_id_var,
                 font=("Consolas", 9, "bold"),
                 fg=C["info"], bg="#090d13").pack(side=tk.RIGHT)

        # Operator
        tk.Label(right, text="Operator:", font=("Segoe UI", 8),
                 fg=C["text_muted"], bg="#090d13").pack(side=tk.RIGHT, padx=(14, 2))
        self._operator_var = tk.StringVar()
        ttk.Entry(right, textvariable=self._operator_var,
                  width=16).pack(side=tk.RIGHT)

    # ═════════════════════════════════════════════════════════════════════════
    # Main 3-column layout
    # ═════════════════════════════════════════════════════════════════════════

    def _build_main(self) -> None:
        # Use pack(side=LEFT) for the 3-column split.
        # grid + columnconfigure(minsize=...) collapses panels on macOS old Tk.
        main = tk.Frame(self.root, bg=COLOURS["bg"])
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=(4, 6))
        self._build_left(main)
        self._build_center(main)
        self._build_right(main)

    # ─────────────────────────────────────────────────────────────────────────
    # LEFT panel
    # ─────────────────────────────────────────────────────────────────────────

    def _build_left(self, parent: tk.Frame) -> None:
        C = COLOURS
        # pack(side=LEFT) is Mac-safe; grid_propagate(False) with fixed width
        # collapses silently on old macOS Tk.
        frame = tk.Frame(parent, bg=C["bg_panel"])
        frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))

        # ── Serial connection ────────────────────────────────────────────────
        conn_sec = SectionFrame(frame, "Serial Connection")
        conn_sec.pack(fill=tk.X, padx=6, pady=6)

        port_row = tk.Frame(conn_sec, bg=C["bg_panel"])
        port_row.pack(fill=tk.X, padx=6, pady=(4, 2))
        tk.Label(port_row, text="Port:", font=("Segoe UI", 8),
                 fg=C["text_muted"], bg=C["bg_panel"]).pack(side=tk.LEFT)
        self._port_var = tk.StringVar(
            value=self.config.get("default_com_port") or SIMULATION_PORT)
        self._port_combo = ttk.Combobox(
            port_row, textvariable=self._port_var, width=13, state="readonly")
        self._port_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(port_row, text="⟳", width=3,
                   command=self._refresh_ports).pack(side=tk.LEFT)
        self._refresh_ports()

        btn_row = tk.Frame(conn_sec, bg=C["bg_panel"])
        btn_row.pack(fill=tk.X, padx=6, pady=2)
        self._connect_btn = ttk.Button(btn_row, text="Connect",
                                       style="Accent.TButton",
                                       command=self._connect)
        self._connect_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._disconnect_btn = ttk.Button(btn_row, text="Disconnect",
                                          command=self._disconnect,
                                          state=tk.DISABLED)
        self._disconnect_btn.pack(side=tk.LEFT)

        # Connection status lamp row
        lamp_row = tk.Frame(conn_sec, bg=C["bg_panel"])
        lamp_row.pack(fill=tk.X, padx=6, pady=(2, 6))
        self._conn_lamp = StatusLamp(lamp_row, size=14)
        self._conn_lamp.pack(side=tk.LEFT, padx=(0, 6))
        self._conn_text_var = tk.StringVar(value="Disconnected")
        tk.Label(lamp_row, textvariable=self._conn_text_var,
                 font=("Segoe UI", 8), fg=C["text_muted"],
                 bg=C["bg_panel"]).pack(side=tk.LEFT)

        # ── Motor control ────────────────────────────────────────────────────
        motor_sec = SectionFrame(frame, "Motor Control")
        motor_sec.pack(fill=tk.X, padx=6, pady=4)

        dir_row = tk.Frame(motor_sec, bg=C["bg_panel"])
        dir_row.pack(fill=tk.X, padx=6, pady=(4, 2))
        self._fwd_btn  = ttk.Button(dir_row, text="▶ Fwd",
                                    command=lambda: self._motor("FORWARD"))
        self._rev_btn  = ttk.Button(dir_row, text="◀ Rev",
                                    command=lambda: self._motor("REVERSE"))
        self._stop_btn = ttk.Button(dir_row, text="■ Stop",
                                    command=lambda: self._motor("STOP"))
        self._home_btn = ttk.Button(dir_row, text="⌂ Home",
                                    command=lambda: self._motor("HOME"))
        for btn in (self._fwd_btn, self._rev_btn, self._stop_btn, self._home_btn):
            btn.pack(side=tk.LEFT, padx=2)

        # ESTOP
        self._estop_btn = ttk.Button(
            motor_sec, text="⛔  EMERGENCY STOP",
            style="Danger.TButton",
            command=self._estop)
        self._estop_btn.pack(fill=tk.X, padx=6, pady=(4, 2))

        # Speed
        spd_row = tk.Frame(motor_sec, bg=C["bg_panel"])
        spd_row.pack(fill=tk.X, padx=6, pady=(2, 6))
        tk.Label(spd_row, text="Speed:", font=("Segoe UI", 8),
                 fg=C["text_muted"], bg=C["bg_panel"]).pack(side=tk.LEFT)
        self._speed_var = tk.IntVar(value=self.config.get("motor_speed", 500))
        self._speed_spin = ttk.Spinbox(
            spd_row, from_=1, to=9999, width=8,
            textvariable=self._speed_var,
            command=self._on_speed_change)
        self._speed_spin.pack(side=tk.LEFT, padx=4)
        tk.Label(spd_row, text="steps/s", font=("Segoe UI", 8),
                 fg=C["text_muted"], bg=C["bg_panel"]).pack(side=tk.LEFT)
        self._speed_spin.bind("<Return>",   lambda _e: self._on_speed_change())
        self._speed_spin.bind("<FocusOut>", lambda _e: self._on_speed_change())

        # Disable motor controls until connected
        self._set_motor_enabled(False)

        # ── Sample control ───────────────────────────────────────────────────
        smp_sec = SectionFrame(frame, "Sample Control")
        smp_sec.pack(fill=tk.X, padx=6, pady=4)

        smp_row = tk.Frame(smp_sec, bg=C["bg_panel"])
        smp_row.pack(fill=tk.X, padx=6, pady=6)
        tk.Label(smp_row, text="# Samples:", font=("Segoe UI", 8),
                 fg=C["text_muted"], bg=C["bg_panel"]).pack(side=tk.LEFT)
        self._n_samples_var = tk.IntVar(value=5)
        ttk.Spinbox(smp_row, from_=1, to=200,
                    textvariable=self._n_samples_var, width=6).pack(side=tk.LEFT, padx=4)
        ttk.Button(smp_row, text="Create",
                   command=self._create_samples).pack(side=tk.LEFT, padx=4)

        # ── Notes ────────────────────────────────────────────────────────────
        notes_sec = SectionFrame(frame, "Notes (optional)")
        notes_sec.pack(fill=tk.X, padx=6, pady=4)
        self._notes = tk.Text(
            notes_sec, height=4, width=28,
            bg=C["bg_section"], fg=C["text"],
            insertbackground=C["text"],
            relief=tk.FLAT, font=("Segoe UI", 8), wrap=tk.WORD)
        self._notes.pack(padx=6, pady=4)

        # ── Bottom actions ───────────────────────────────────────────────────
        ttk.Button(frame, text="⚙  Settings",
                   command=self._open_settings).pack(
            fill=tk.X, padx=6, pady=(6, 2))
        ttk.Button(frame, text="🗑  New Session",
                   command=self._new_session).pack(
            fill=tk.X, padx=6, pady=(0, 8))

    # ─────────────────────────────────────────────────────────────────────────
    # CENTER panel
    # ─────────────────────────────────────────────────────────────────────────

    def _build_center(self, parent: tk.Frame) -> None:
        C = COLOURS
        frame = tk.Frame(parent, bg=C["bg"])
        frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
        frame.rowconfigure(0, weight=3)
        frame.rowconfigure(1, weight=2)
        frame.columnconfigure(0, weight=1)

        # ── Live graph ───────────────────────────────────────────────────────
        graph_lf = SectionFrame(frame, "Live Force vs Time")
        graph_lf.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        graph_lf.rowconfigure(0, weight=1)
        graph_lf.columnconfigure(0, weight=1)
        self._live_graph = LiveForceGraph(
            graph_lf,
            history_length=self.config.get("graph_history_length", 500))
        self._live_graph.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # ── Sample table ─────────────────────────────────────────────────────
        table_lf = SectionFrame(frame, "Sample List")
        table_lf.grid(row=1, column=0, sticky="nsew")

        cols = ("name", "status", "peak")
        self._tree = ttk.Treeview(table_lf, columns=cols,
                                   show="headings", height=7)
        self._tree.heading("name",   text="Sample Name")
        self._tree.heading("status", text="Status")
        self._tree.heading("peak",   text="Peak Force (N)")
        self._tree.column("name",   width=170)
        self._tree.column("status", width=90,  anchor=tk.CENTER)
        self._tree.column("peak",   width=120, anchor=tk.CENTER)

        self._tree.tag_configure("pending",  foreground=C["text_muted"])
        self._tree.tag_configure("running",  foreground=C["warning"])
        self._tree.tag_configure("complete", foreground=C["success"])

        vsb = ttk.Scrollbar(table_lf, orient="vertical",
                             command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                        padx=(4, 0), pady=4)
        vsb.pack(side=tk.RIGHT, fill=tk.Y, pady=4, padx=(0, 4))

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        action_row = tk.Frame(table_lf, bg=C["bg_panel"])
        action_row.pack(fill=tk.X, padx=4, pady=(0, 4))

        self._run_btn = ttk.Button(action_row,
                                   text="▶  Run Selected Sample",
                                   style="Accent.TButton",
                                   command=self._run_selected,
                                   state=tk.DISABLED)
        self._run_btn.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(action_row, text="⏹ Abort",
                   style="Warning.TButton",
                   command=self._abort).pack(side=tk.LEFT)

    # ─────────────────────────────────────────────────────────────────────────
    # RIGHT panel
    # ─────────────────────────────────────────────────────────────────────────

    def _build_right(self, parent: tk.Frame) -> None:
        C = COLOURS
        frame = tk.Frame(parent, bg=C["bg_panel"])
        frame.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 0))
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(3, weight=1)

        # ── Force value displays ─────────────────────────────────────────────
        self._force_disp = BigValueDisplay(frame, "CURRENT FORCE", "N",
                                           colour=C["success"])
        self._force_disp.grid(row=0, column=0, padx=4, pady=(8, 4), sticky="ew")

        self._peak_disp = BigValueDisplay(frame, "PEAK FORCE", "N",
                                          colour=C["danger"])
        self._peak_disp.grid(row=0, column=1, padx=4, pady=(8, 4), sticky="ew")

        # ── Status lamps ─────────────────────────────────────────────────────
        status_lf = SectionFrame(frame, "System Status")
        status_lf.grid(row=1, column=0, columnspan=2, sticky="ew",
                       padx=6, pady=4)

        def _add_lamp_row(label: str, lamp_attr: str, val_attr: str = None,
                          init_val: str = "—"):
            row = tk.Frame(status_lf, bg=C["bg_panel"])
            row.pack(fill=tk.X, padx=8, pady=3)
            lamp = StatusLamp(row, size=14)
            lamp.pack(side=tk.LEFT, padx=(0, 8))
            setattr(self, lamp_attr, lamp)
            tk.Label(row, text=label, font=("Segoe UI", 8),
                     fg=C["text_muted"], bg=C["bg_panel"],
                     width=15, anchor=tk.W).pack(side=tk.LEFT)
            if val_attr:
                var = tk.StringVar(value=init_val)
                tk.Label(row, textvariable=var,
                         font=("Segoe UI", 8, "bold"),
                         fg=C["text"], bg=C["bg_panel"]).pack(side=tk.LEFT)
                setattr(self, val_attr, var)

        _add_lamp_row("Connection",      "_slamp_conn")
        _add_lamp_row("Motor",           "_slamp_motor",  "_svar_motor",  "Stopped")
        _add_lamp_row("Limit Switch 1",  "_slamp_lim1",   "_svar_lim1",   "OK")
        _add_lamp_row("Limit Switch 2",  "_slamp_lim2",   "_svar_lim2",   "OK")
        _add_lamp_row("Test",            "_slamp_test",   "_svar_test",   "Idle")

        # Initial lamp colours
        self._slamp_conn.set_colour("grey")
        self._slamp_motor.set_colour("grey")
        self._slamp_lim1.set_colour("green")
        self._slamp_lim2.set_colour("green")
        self._slamp_test.set_colour("grey")

        # ── Progress bar ─────────────────────────────────────────────────────
        prog_lf = SectionFrame(frame, "Test Progress")
        prog_lf.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=4)

        self._progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(
            prog_lf, variable=self._progress_var,
            maximum=100, mode="determinate",
            style="Horizontal.TProgressbar",
        ).pack(fill=tk.X, padx=6, pady=(4, 2))

        self._progress_text_var = tk.StringVar(value="Idle")
        tk.Label(prog_lf, textvariable=self._progress_text_var,
                 font=("Segoe UI", 8), fg=C["text_muted"],
                 bg=C["bg_panel"]).pack(pady=(0, 4))

        # ── Comparison graph ─────────────────────────────────────────────────
        comp_lf = SectionFrame(frame, "Sample Comparison")
        comp_lf.grid(row=3, column=0, columnspan=2, sticky="nsew",
                     padx=6, pady=4)
        self._comparison_graph = ComparisonGraph(comp_lf)
        self._comparison_graph.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # ── Export ───────────────────────────────────────────────────────────
        exp_row = tk.Frame(frame, bg=C["bg_panel"])
        exp_row.grid(row=4, column=0, columnspan=2, sticky="ew",
                     padx=6, pady=(0, 8))
        ttk.Button(exp_row, text="📄 Export CSV",
                   command=self._export_csv).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(exp_row, text="📊 Export Excel",
                   command=self._export_excel).pack(side=tk.LEFT)

    # ═════════════════════════════════════════════════════════════════════════
    # Serial callbacks  (all called from main thread via poll())
    # ═════════════════════════════════════════════════════════════════════════

    def _cb_ready(self, _) -> None:
        self._conn_text_var.set("Connected")
        self._conn_lamp.set_colour("green")
        self._slamp_conn.set_colour("green")
        self._connect_btn.config(state=tk.DISABLED)
        self._disconnect_btn.config(state=tk.NORMAL)
        self._set_motor_enabled(True)

    def _cb_limit1(self, _) -> None:
        self._slamp_lim1.set_colour("red")
        self._svar_lim1.set("TRIPPED")
        self._limit_active = True
        self._set_motor_enabled(False)
        messagebox.showwarning(
            "Limit Switch 1",
            "Limit Switch 1 has been activated!\nAll motor movement stopped.")

    def _cb_limit2(self, _) -> None:
        self._slamp_lim2.set_colour("red")
        self._svar_lim2.set("TRIPPED")
        self._limit_active = True
        self._set_motor_enabled(False)
        messagebox.showwarning(
            "Limit Switch 2",
            "Limit Switch 2 has been activated!\nAll motor movement stopped.")

    def _cb_error(self, msg) -> None:
        messagebox.showerror("Device Error", str(msg or "Unknown error"))

    # ═════════════════════════════════════════════════════════════════════════
    # TestSequence callbacks
    # ═════════════════════════════════════════════════════════════════════════

    def _on_progress(self, state: str) -> None:
        _pct = {"Idle": 0, "Running Test": 40,
                "Stopping Motor": 70, "Saving Result": 85, "Completed": 100}
        self._progress_text_var.set(state)
        self._progress_var.set(_pct.get(state, 0))
        self._svar_test.set(state)

        if state == "Running Test":
            self._slamp_test.set_colour("yellow")
            self._slamp_motor.set_colour("green")
            self._svar_motor.set("Running")
        elif state == "Idle":
            self._slamp_test.set_colour("grey")
            self._slamp_motor.set_colour("grey")
            self._svar_motor.set("Stopped")
        elif state == "Completed":
            self._slamp_test.set_colour("green")
            self._slamp_motor.set_colour("grey")
            self._svar_motor.set("Stopped")
            self._refresh_table()

    def _on_force_update(self, force: float, peak: float) -> None:
        self._force_disp.set_value(force)
        self._peak_disp.set_value(peak)

    def _on_sample_complete(self, idx: int, peak: float) -> None:
        self._refresh_table()
        # Re-enable Run button if selection still points to a pending sample
        self._on_tree_select(None)

    def _on_error(self, msg: str) -> None:
        messagebox.showerror("Test Error", msg)

    # ═════════════════════════════════════════════════════════════════════════
    # UI actions
    # ═════════════════════════════════════════════════════════════════════════

    def _refresh_ports(self) -> None:
        ports = list_ports()
        self._port_combo["values"] = ports
        if self._port_var.get() not in ports:
            self._port_var.set(ports[0] if ports else "")

    def _connect(self) -> None:
        port = self._port_var.get()
        baud = int(self.config.get("baud_rate", 115200))
        self._comm.set_sim_peak_drop(
            float(self.config.get("peak_drop_threshold", 0.5)))
        ok = self._comm.connect(port, baud)
        if not ok:
            messagebox.showerror(
                "Connection Failed",
                f"Could not open {port} at {baud} baud.\n"
                "Check the port and try again, or use SIMULATION.")
            return
        self.config.set("default_com_port", port)
        self.config.save()

    def _disconnect(self) -> None:
        if self._sequence.is_running():
            self._sequence.abort()
        self._comm.disconnect()
        self._conn_text_var.set("Disconnected")
        self._conn_lamp.set_colour("grey")
        self._slamp_conn.set_colour("grey")
        self._connect_btn.config(state=tk.NORMAL)
        self._disconnect_btn.config(state=tk.DISABLED)
        self._set_motor_enabled(False)
        self._force_disp.set_value(None)
        self._peak_disp.set_value(None)

    def _motor(self, cmd: str) -> None:
        if self._limit_active and cmd not in ("HOME", "STOP"):
            messagebox.showwarning(
                "Limit Switch Active",
                "A limit switch is tripped.\nUse HOME or clear the condition first.")
            return
        self._comm.send(cmd)
        if cmd == "STOP":
            self._slamp_motor.set_colour("grey")
            self._svar_motor.set("Stopped")
        else:
            self._slamp_motor.set_colour("green")
            self._svar_motor.set(cmd.title())

    def _on_speed_change(self) -> None:
        try:
            speed = int(self._speed_var.get())
            self.config.set("motor_speed", speed)
            if self._comm.is_connected():
                self._comm.send(f"SPEED:{speed}")
        except (ValueError, tk.TclError):
            pass

    def _estop(self) -> None:
        self._sequence.estop()
        self._slamp_motor.set_colour("red")
        self._svar_motor.set("E-STOP")
        self._slamp_test.set_colour("red")
        messagebox.showwarning(
            "Emergency Stop",
            "Emergency stop activated.\nAll motion halted immediately.")

    def _create_samples(self) -> None:
        try:
            n = int(self._n_samples_var.get())
            if n < 1:
                raise ValueError
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid", "Enter a valid number of samples (≥ 1).")
            return
        self._samples.create_samples(n)
        self._refresh_table()
        self._selected_idx = None
        self._run_btn.config(state=tk.DISABLED)

    def _refresh_table(self) -> None:
        self._tree.delete(*self._tree.get_children())
        for i, s in enumerate(self._samples.get_all()):
            peak_str = f"{s.peak_force:.3f}" if s.peak_force is not None else "—"
            tag = s.status.lower()
            self._tree.insert("", tk.END, iid=str(i),
                               values=(s.name, s.status, peak_str),
                               tags=(tag,))

    def _on_tree_select(self, _event) -> None:
        sel = self._tree.selection()
        if not sel:
            self._run_btn.config(state=tk.DISABLED)
            return
        self._selected_idx = int(sel[0])
        sample = self._samples.get_sample(self._selected_idx)
        can_run = (
            sample is not None
            and sample.status == "Pending"
            and self._comm.is_connected()
            and not self._sequence.is_running()
            and not self._limit_active
        )
        self._run_btn.config(state=tk.NORMAL if can_run else tk.DISABLED)

    def _run_selected(self) -> None:
        if self._selected_idx is None:
            messagebox.showinfo("No Selection", "Select a sample from the list first.")
            return
        if not self._comm.is_connected():
            messagebox.showwarning("Not Connected",
                                   "Connect to the ESP32 before running a test.")
            return
        if self._sequence.is_running():
            messagebox.showwarning("Busy", "A test is already in progress.")
            return
        if self._limit_active:
            messagebox.showwarning("Limit Active",
                                   "Clear the limit switch condition first.")
            return
        sample = self._samples.get_sample(self._selected_idx)
        if sample is None or sample.status != "Pending":
            messagebox.showinfo("Invalid", "Select a Pending sample.")
            return

        operator = self._operator_var.get().strip() or "Unknown"
        self._run_btn.config(state=tk.DISABLED)
        started = self._sequence.start(self._selected_idx, operator, self._test_id)
        if started:
            self._refresh_table()

    def _abort(self) -> None:
        self._sequence.abort()
        self._slamp_motor.set_colour("grey")
        self._svar_motor.set("Stopped")
        self._refresh_table()

    def _new_session(self) -> None:
        if not messagebox.askyesno(
                "New Session",
                "Start a new session?\nAll current samples and results will be cleared."):
            return
        if self._sequence.is_running():
            self._sequence.abort()
        self._samples.clear()
        self._logger.clear()
        self._test_id = self._new_test_id()
        self._test_id_var.set(self._test_id)
        self._selected_idx = None
        self._limit_active = False
        self._refresh_table()
        self._live_graph.reset()
        self._comparison_graph.update([], [])
        self._force_disp.set_value(None)
        self._peak_disp.set_value(None)
        self._progress_var.set(0)
        self._progress_text_var.set("Idle")
        self._slamp_lim1.set_colour("green")
        self._slamp_lim2.set_colour("green")
        self._svar_lim1.set("OK")
        self._svar_lim2.set("OK")
        self._slamp_test.set_colour("grey")
        self._svar_test.set("Idle")
        self._run_btn.config(state=tk.DISABLED)

    # ─── Export ──────────────────────────────────────────────────────────────

    def _export_csv(self) -> None:
        if not self._logger.get_records():
            messagebox.showinfo("No Data", "No completed tests to export yet.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if path:
            out = self._logger.export_csv(path)
            messagebox.showinfo("Export Successful", f"Saved:\n{out}")

    def _export_excel(self) -> None:
        if not self._logger.get_records():
            messagebox.showinfo("No Data", "No completed tests to export yet.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
            initialfile=f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        )
        if path:
            try:
                out = self._logger.export_excel(path)
                messagebox.showinfo("Export Successful", f"Saved:\n{out}")
            except ImportError as exc:
                messagebox.showerror("Missing Library", str(exc))

    # ─── Settings dialog ──────────────────────────────────────────────────────

    def _open_settings(self) -> None:
        SettingsDialog(self.root, self.config, self._apply_settings)

    def _apply_settings(self) -> None:
        self._logger.set_export_folder(self.config.get("export_folder", "./exports"))
        self._live_graph.set_history_length(
            int(self.config.get("graph_history_length", 500)))
        self._comm.set_sim_peak_drop(
            float(self.config.get("peak_drop_threshold", 0.5)))
        if self._comm.is_connected():
            self._comm.send(f"SPEED:{self.config.get('motor_speed', 500)}")

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _set_motor_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for btn in (self._fwd_btn, self._rev_btn, self._stop_btn,
                    self._home_btn, self._estop_btn):
            btn.config(state=state)

    @staticmethod
    def _new_test_id() -> str:
        return f"T-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:6].upper()}"

    def _force_graph_redraw(self) -> None:
        """
        Forcibly redraws both matplotlib canvases after the Tk event loop has
        started.  On macOS, TkAgg canvases are blank until the event loop is
        running and a draw() call is explicitly made post-map.
        """
        try:
            self._live_graph._canvas.draw()
            self._comparison_graph._canvas.draw()
            self.root.update_idletasks()
            # Bring window to front on Mac (it can open behind other windows)
            if sys.platform == "darwin":
                self.root.lift()
                self.root.focus_force()
        except Exception:
            pass

    def _tick_clock(self) -> None:
        self._clock_var.set(datetime.now().strftime("%Y-%m-%d   %H:%M:%S"))
        self.root.after(1000, self._tick_clock)

    def _poll_serial(self) -> None:
        self._comm.poll()
        self.root.after(_POLL_MS, self._poll_serial)


# ═════════════════════════════════════════════════════════════════════════════
# Settings dialog (modal Toplevel)
# ═════════════════════════════════════════════════════════════════════════════

class SettingsDialog(tk.Toplevel):
    """
    Modal settings window.  Edits ConfigManager directly and calls
    *on_save* after persisting changes.
    """

    # (display label, config key, Python type)
    FIELDS = [
        ("Baud Rate",                    "baud_rate",            int),
        ("Motor Speed (steps/s)",        "motor_speed",          int),
        ("Peak Drop Threshold (N)",      "peak_drop_threshold",  float),
        ("Calibration Factor",           "calibration_factor",   float),
        ("Sampling Rate (Hz)",           "sampling_rate",        int),
        ("Export Folder",                "export_folder",        str),
        ("Graph History Length (pts)",   "graph_history_length", int),
    ]

    def __init__(self, parent: tk.Tk, config: ConfigManager,
                 on_save=None) -> None:
        super().__init__(parent)
        C = COLOURS
        self.config  = config
        self.on_save = on_save
        self.title("Settings")
        self.geometry("440x390")
        self.resizable(False, False)
        self.configure(bg=C["bg_panel"])
        self.grab_set()   # modal

        self._vars: dict = {}
        self._build(C)

    def _build(self, C: dict) -> None:
        tk.Label(
            self, text="Application Settings",
            font=("Segoe UI", 11, "bold"),
            fg=C["text"], bg=C["bg_panel"],
        ).pack(pady=(14, 6))

        tk.Frame(self, bg=C["border"], height=1).pack(fill=tk.X, padx=16)

        grid = tk.Frame(self, bg=C["bg_panel"])
        grid.pack(fill=tk.BOTH, expand=True, padx=16, pady=10)
        grid.columnconfigure(1, weight=1)

        for row, (label, key, typ) in enumerate(self.FIELDS):
            tk.Label(grid, text=label, font=("Segoe UI", 9),
                     fg=C["text_muted"], bg=C["bg_panel"],
                     anchor=tk.W).grid(row=row, column=0, sticky=tk.W,
                                       pady=4, padx=(0, 12))
            var = tk.StringVar(value=str(self.config.get(key, "")))
            self._vars[key] = (var, typ)
            ttk.Entry(grid, textvariable=var, width=24).grid(
                row=row, column=1, pady=4, sticky=tk.EW)

        tk.Frame(self, bg=C["border"], height=1).pack(fill=tk.X, padx=16)

        btn_row = tk.Frame(self, bg=C["bg_panel"])
        btn_row.pack(fill=tk.X, padx=16, pady=10)
        ttk.Button(btn_row, text="Save & Apply",
                   style="Accent.TButton",
                   command=self._save).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(btn_row, text="Cancel",
                   command=self.destroy).pack(side=tk.LEFT)

    def _save(self) -> None:
        for key, (var, typ) in self._vars.items():
            try:
                self.config.set(key, typ(var.get()))
            except (ValueError, TypeError):
                pass
        self.config.save()
        if self.on_save:
            self.on_save()
        self.destroy()
