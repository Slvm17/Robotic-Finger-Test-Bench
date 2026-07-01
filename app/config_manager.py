"""
Configuration manager for LoadCellTester.
Loads and saves user-adjustable settings from config.json.
The file lives alongside main.py (or the PyInstaller executable).
"""
import json
import os
from typing import Any, Dict


DEFAULT_CONFIG: Dict[str, Any] = {
    "default_com_port": "",
    "baud_rate": 115200,
    "motor_speed": 500,
    "peak_drop_threshold": 0.5,
    "calibration_factor": 1.0,
    "sampling_rate": 10,
    "export_folder": "./exports",
    "graph_history_length": 500,
}


class ConfigManager:
    """Manages application configuration stored in config.json."""

    def __init__(self) -> None:
        # Resolve config path relative to this file's parent (project root)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._config_path = os.path.join(base_dir, "config.json")
        self._config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self._load()

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load config from file, merging with defaults for any missing keys."""
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                for key in DEFAULT_CONFIG:
                    if key in data:
                        self._config[key] = data[key]
            except Exception as exc:
                print(f"[Config] Could not load config.json: {exc}")
        else:
            self.save()  # Create defaults on first run

    def save(self) -> None:
        """Persist current configuration to config.json."""
        try:
            with open(self._config_path, "w", encoding="utf-8") as fh:
                json.dump(self._config, fh, indent=4)
        except Exception as exc:
            print(f"[Config] Failed to save: {exc}")

    # ── Access ────────────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._config[key] = value

    def get_all(self) -> Dict[str, Any]:
        return dict(self._config)

    def update_all(self, data: Dict[str, Any]) -> None:
        """Bulk-update config keys and immediately save."""
        self._config.update(data)
        self.save()
