"""
Test sequence state machine for LoadCellTester.

TestSequence orchestrates one sample test from start to completion:
  1. Validates preconditions.
  2. Sends START to the ESP32 / simulation.
  3. Listens for FORCE callbacks → updates live graph + force displays.
  4. On PEAK received → captures final peak value.
  5. On DONE received  → saves record, marks sample Complete,
                          updates comparison graph, fires on_complete.
  6. On LIMIT or ERROR → aborts gracefully.

All public methods and all callbacks execute on the Tkinter main thread
(callbacks are dispatched by SerialComm.poll() which is driven by root.after).
"""

import time
from datetime import datetime
from typing import Callable, Optional

from app.serial_comm import SerialComm
from app.sample_manager import SampleManager
from app.data_logger import DataLogger
from app.graphs import LiveForceGraph, ComparisonGraph


class TestSequence:

    def __init__(
        self,
        serial_comm: SerialComm,
        sample_manager: SampleManager,
        data_logger: DataLogger,
        live_graph: LiveForceGraph,
        comparison_graph: ComparisonGraph,
        on_progress: Callable[[str], None],
        on_force_update: Callable[[float, float], None],  # (current_force, peak_force)
        on_complete: Callable[[int, float], None],         # (sample_idx, peak_force)
        on_error: Callable[[str], None],
    ) -> None:
        self._comm            = serial_comm
        self._samples         = sample_manager
        self._logger          = data_logger
        self._live_graph      = live_graph
        self._comparison_graph = comparison_graph
        self._on_progress     = on_progress
        self._on_force_update = on_force_update
        self._on_complete     = on_complete
        self._on_error        = on_error

        self._running          = False
        self._current_idx: Optional[int] = None
        self._start_time: Optional[float] = None
        self._current_force: float = 0.0
        self._peak_force: float = 0.0
        self._operator: str = ""
        self._test_id: str = ""

        # Register serial callbacks (called from main thread via poll)
        self._comm.register_callback("FORCE", self._on_force)
        self._comm.register_callback("PEAK",  self._on_peak)
        self._comm.register_callback("DONE",  self._on_done)
        self._comm.register_callback("LIMIT1", self._on_limit)
        self._comm.register_callback("LIMIT2", self._on_limit)
        self._comm.register_callback("ERROR",  self._on_comm_error)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, sample_idx: int, operator: str, test_id: str) -> bool:
        """Begin a test run for the sample at *sample_idx*."""
        if self._running:
            return False

        sample = self._samples.get_sample(sample_idx)
        if sample is None:
            self._on_error(f"Sample index {sample_idx} not found.")
            return False
        if sample.status != "Pending":
            self._on_error(f"Sample '{sample.name}' is not Pending.")
            return False

        self._running       = True
        self._current_idx   = sample_idx
        self._start_time    = time.time()
        self._current_force = 0.0
        self._peak_force    = 0.0
        self._operator      = operator
        self._test_id       = test_id

        self._samples.set_running(sample_idx)
        self._live_graph.reset()
        self._on_progress("Running Test")
        self._comm.send("START")
        return True

    def abort(self) -> None:
        """Abort the current test (e.g. user pressed Abort or limit switch)."""
        if not self._running:
            return
        self._running = False
        self._comm.send("STOP")
        if self._current_idx is not None:
            self._samples.set_pending(self._current_idx)
            self._current_idx = None
        self._on_progress("Idle")

    def estop(self) -> None:
        """Emergency stop — immediately halts everything."""
        self._running = False
        self._comm.send("ESTOP")
        if self._current_idx is not None:
            self._samples.set_pending(self._current_idx)
            self._current_idx = None
        self._on_progress("Idle")

    def is_running(self) -> bool:
        return self._running

    # ── Serial callbacks (Tkinter main thread) ────────────────────────────────

    def _on_force(self, value: float) -> None:
        if not self._running:
            return
        self._current_force = value
        if value > self._peak_force:
            self._peak_force = value

        elapsed = time.time() - (self._start_time or time.time())
        self._live_graph.update(elapsed, value)
        self._on_force_update(self._current_force, self._peak_force)

    def _on_peak(self, value: float) -> None:
        """ESP32 reports the authoritative peak value at test completion."""
        self._peak_force = value
        self._on_force_update(self._current_force, self._peak_force)

    def _on_done(self, _) -> None:
        if not self._running:
            return
        self._running = False
        idx = self._current_idx
        self._current_idx = None

        self._on_progress("Stopping Motor")
        self._on_progress("Saving Result")

        sample = self._samples.get_sample(idx)
        if sample is not None and self._peak_force > 0:
            self._logger.add_record(
                test_id=self._test_id,
                operator=self._operator,
                sample_name=sample.name,
                peak_force=self._peak_force,
                timestamp=datetime.now(),
            )
            self._samples.set_complete(idx, self._peak_force)
            self._on_complete(idx, self._peak_force)

        # Refresh comparison graph with all completed samples
        completed = self._samples.get_completed()
        if completed:
            self._comparison_graph.update(
                [s.name for s in completed],
                [s.peak_force for s in completed],
            )

        self._on_progress("Completed")

    def _on_limit(self, _) -> None:
        """Limit switch fired — abort the running test."""
        if self._running:
            self.abort()

    def _on_comm_error(self, msg) -> None:
        if self._running:
            self.abort()
        self._on_error(str(msg) if msg else "Communication error")
