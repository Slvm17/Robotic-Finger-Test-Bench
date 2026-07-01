"""
Sample management for LoadCellTester.
Holds the list of samples for a test session and tracks their state.
"""
import uuid
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Sample:
    name: str
    status: str = "Pending"          # "Pending" | "Running" | "Complete"
    peak_force: Optional[float] = None
    sample_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8].upper())


class SampleManager:
    """Manages the ordered list of samples for one test session."""

    def __init__(self) -> None:
        self._samples: List[Sample] = []

    # ── Construction ──────────────────────────────────────────────────────────

    def create_samples(self, n: int, prefix: str = "Sample") -> None:
        """Replace the current list with n fresh Pending samples."""
        self._samples = [Sample(name=f"{prefix} {i + 1}") for i in range(n)]

    def clear(self) -> None:
        self._samples = []

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_all(self) -> List[Sample]:
        return list(self._samples)

    def get_sample(self, index: int) -> Optional[Sample]:
        if 0 <= index < len(self._samples):
            return self._samples[index]
        return None

    def get_completed(self) -> List[Sample]:
        return [s for s in self._samples if s.status == "Complete"]

    def count(self) -> int:
        return len(self._samples)

    # ── State transitions ─────────────────────────────────────────────────────

    def set_running(self, index: int) -> None:
        """Mark sample at *index* as Running; reset any previously Running sample."""
        for s in self._samples:
            if s.status == "Running":
                s.status = "Pending"
        if 0 <= index < len(self._samples):
            self._samples[index].status = "Running"

    def set_complete(self, index: int, peak_force: float) -> None:
        if 0 <= index < len(self._samples):
            self._samples[index].status = "Complete"
            self._samples[index].peak_force = round(peak_force, 4)

    def set_pending(self, index: int) -> None:
        """Revert a sample back to Pending (e.g., after an aborted test)."""
        if 0 <= index < len(self._samples):
            self._samples[index].status = "Pending"
            self._samples[index].peak_force = None
