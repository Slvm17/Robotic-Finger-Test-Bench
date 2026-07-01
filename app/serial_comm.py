"""
Serial communication layer for LoadCellTester.

Supports two modes:
  - Real mode  : opens a pyserial port and reads/writes to the ESP32.
  - Simulation : generates realistic synthetic load-cell data entirely in
                 software so the UI can be developed and tested without
                 any hardware connected.

Thread model
------------
A background thread runs the I/O loop and puts parsed messages onto a
thread-safe queue.  The Tkinter main thread calls poll() (via root.after)
to drain that queue and invoke registered callbacks — ensuring all UI
updates happen on the correct thread.
"""

import math
import queue
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False


# Sentinel value used to select simulation mode in the port dropdown
SIMULATION_PORT = "SIMULATION"


# ── Port enumeration ──────────────────────────────────────────────────────────

def list_ports() -> List[str]:
    """Return available COM port names, always prepended by SIMULATION."""
    ports: List[str] = [SIMULATION_PORT]
    if SERIAL_AVAILABLE:
        ports += [p.device for p in serial.tools.list_ports.comports()]
    return ports


# ── SerialComm ────────────────────────────────────────────────────────────────

class SerialComm:
    """
    Thread-safe serial communication handler.

    Protocol (ESP32 → PC)
    ---------------------
    READY           device ready
    FORCE:<float>   current load cell reading in Newtons
    PEAK:<float>    peak force detected (sent once at test completion)
    LIMIT1          limit switch 1 tripped
    LIMIT2          limit switch 2 tripped
    DONE            test cycle complete
    ERROR[:<msg>]   device error

    Protocol (PC → ESP32)
    ---------------------
    CONNECT  START  STOP  FORWARD  REVERSE  HOME  ESTOP  SPEED:<int>
    """

    def __init__(self) -> None:
        self._port: Optional[str] = None
        self._baud: int = 115200
        self._serial: Optional[Any] = None  # serial.Serial instance
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._simulate = False
        self._connected = False

        # Thread-safe message queue (background → main thread)
        self._rx_queue: queue.Queue = queue.Queue()

        # Thread-safe command queue (main thread → simulation loop)
        self._sim_cmd_queue: queue.Queue = queue.Queue()

        # Registered callbacks  {msg_type: [callables]}
        self._callbacks: Dict[str, List[Callable]] = {}

        # Simulation internal state
        self._sim_state: str = "idle"          # idle | running | done
        self._sim_start_time: float = 0.0
        self._sim_target_peak: float = 0.0
        self._sim_ramp_time: float = 5.0
        self._sim_peak_reached: float = 0.0
        self._sim_post_peak_time: float = 0.0
        self._sim_post_peak_started: bool = False
        self._sim_peak_drop_threshold: float = 0.5

    # ── Public API ────────────────────────────────────────────────────────────

    def register_callback(self, msg_type: str, cb: Callable) -> None:
        """Register *cb* to be called whenever a message of *msg_type* arrives."""
        self._callbacks.setdefault(msg_type, []).append(cb)

    def connect(self, port: str, baud: int = 115200) -> bool:
        """
        Open the connection (real or simulated).
        Returns True on success.  READY is pushed to the queue automatically.
        """
        if self._connected:
            self.disconnect()

        self._port = port
        self._baud = baud
        self._simulate = (port == SIMULATION_PORT)

        if not self._simulate:
            if not SERIAL_AVAILABLE:
                self._push("ERROR", "pyserial is not installed (pip install pyserial)")
                return False
            try:
                self._serial = serial.Serial(port, baud, timeout=1)
            except Exception as exc:
                self._push("ERROR", str(exc))
                return False

        self._running = True
        self._connected = True
        target = self._run_simulation if self._simulate else self._run_serial
        self._thread = threading.Thread(target=target, daemon=True, name="SerialThread")
        self._thread.start()
        # Announce device ready
        self._push("READY", None)
        return True

    def disconnect(self) -> None:
        """Close the connection and stop the background thread."""
        self._running = False
        self._connected = False
        self._sim_state = "idle"
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None

    def is_connected(self) -> bool:
        return self._connected

    def send(self, command: str) -> None:
        """Send a command to the ESP32 (or feed it into the simulation engine)."""
        command = command.strip()
        if self._simulate:
            self._sim_cmd_queue.put(command)
        else:
            if self._serial and self._serial.is_open:
                try:
                    self._serial.write((command + "\n").encode("utf-8"))
                except Exception as exc:
                    self._push("ERROR", str(exc))

    def poll(self) -> None:
        """
        Drain the RX queue and dispatch callbacks.
        Must be called exclusively from the Tkinter main thread (via root.after).
        """
        while True:
            try:
                msg_type, value = self._rx_queue.get_nowait()
                for cb in self._callbacks.get(msg_type, []):
                    try:
                        cb(value)
                    except Exception as exc:
                        print(f"[SerialComm] Callback error ({msg_type}): {exc}")
            except queue.Empty:
                break

    def set_sim_peak_drop(self, threshold: float) -> None:
        self._sim_peak_drop_threshold = threshold

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _push(self, msg_type: str, value: Any) -> None:
        self._rx_queue.put((msg_type, value))

    def _parse_line(self, line: str) -> None:
        """Parse one text line received from the ESP32."""
        line = line.strip()
        if not line:
            return
        if line == "READY":
            self._push("READY", None)
        elif line.startswith("FORCE:"):
            try:
                self._push("FORCE", float(line[6:]))
            except ValueError:
                pass
        elif line.startswith("PEAK:"):
            try:
                self._push("PEAK", float(line[5:]))
            except ValueError:
                pass
        elif line == "LIMIT1":
            self._push("LIMIT1", None)
        elif line == "LIMIT2":
            self._push("LIMIT2", None)
        elif line == "DONE":
            self._push("DONE", None)
        elif line.startswith("ERROR"):
            self._push("ERROR", line[6:] if ":" in line else "Device error")

    # ── Real serial loop ──────────────────────────────────────────────────────

    def _run_serial(self) -> None:
        while self._running:
            try:
                if self._serial and self._serial.is_open:
                    raw = self._serial.readline()
                    if raw:
                        self._parse_line(raw.decode("utf-8", errors="ignore"))
            except Exception as exc:
                self._push("ERROR", str(exc))
                self._connected = False
                break

    # ── Simulation loop ───────────────────────────────────────────────────────

    def _process_sim_command(self, cmd: str) -> None:
        if cmd == "START":
            self._sim_state = "running"
            self._sim_start_time = time.time()
            self._sim_peak_reached = 0.0
            self._sim_post_peak_started = False
            # Randomise the synthetic test profile
            self._sim_target_peak = random.uniform(60.0, 220.0)
            self._sim_ramp_time = random.uniform(3.0, 8.0)
        elif cmd in ("STOP", "ESTOP"):
            self._sim_state = "idle"
        elif cmd.startswith("SPEED:"):
            pass  # Speed affects nothing in simulation
        elif cmd == "HOME":
            self._sim_state = "idle"

    def _run_simulation(self) -> None:
        """
        Generates synthetic force data that mimics a real pull-to-failure test:
          1. Sinusoidal ramp up to a random peak.
          2. Short plateau.
          3. Exponential drop after yield point.
          4. When drop exceeds threshold → emit PEAK + DONE.
        """
        interval = 0.05  # 20 Hz sample rate for smooth curves

        while self._running:
            time.sleep(interval)

            # Drain command queue
            while True:
                try:
                    cmd = self._sim_cmd_queue.get_nowait()
                    self._process_sim_command(cmd)
                except queue.Empty:
                    break

            if self._sim_state != "running":
                continue

            elapsed = time.time() - self._sim_start_time
            ramp = self._sim_ramp_time

            if not self._sim_post_peak_started:
                # ── Ramp-up phase ──────────────────────────────────────────
                t_norm = min(elapsed / ramp, 1.0)
                force = self._sim_target_peak * math.sin(math.pi / 2 * t_norm)
                force += random.gauss(0, 0.4)   # realistic noise
                force = max(0.0, force)
                if force > self._sim_peak_reached:
                    self._sim_peak_reached = force

                # Transition to post-peak once we've been climbing for ramp_time
                if elapsed >= ramp:
                    self._sim_post_peak_started = True
                    self._sim_post_peak_time = time.time()
            else:
                # ── Post-peak drop phase ───────────────────────────────────
                drop_elapsed = time.time() - self._sim_post_peak_time
                force = self._sim_peak_reached * math.exp(-0.9 * drop_elapsed)
                force += random.gauss(0, 0.2)
                force = max(0.0, force)

                drop = self._sim_peak_reached - force
                if drop >= self._sim_peak_drop_threshold:
                    # Test complete — emit peak then done
                    self._sim_state = "done"
                    self._push("PEAK", round(self._sim_peak_reached, 3))
                    self._push("DONE", None)
                    continue

            self._push("FORCE", round(force, 3))
