from __future__ import annotations
import threading
from dataclasses import dataclass

@dataclass(frozen=True)
class SpeedState:
    enabled: bool
    speed: float
    version: int

class SpeedController:
    """
    Thread-safe speed settings (enabled + speed factor).
    Use ONE instance per tab (Mãe / Filha) se você quer controle separado.
    """
    def __init__(self, enabled: bool = False, speed: float = 2.0):
        self._lock = threading.Lock()
        self._enabled = bool(enabled)
        self._speed = float(speed)
        self._version = 1

    def get(self) -> SpeedState:
        with self._lock:
            return SpeedState(self._enabled, self._speed, self._version)

    def set(self, enabled: bool | None = None, speed: float | None = None) -> SpeedState:
        with self._lock:
            changed = False
            if enabled is not None:
                enabled = bool(enabled)
                if enabled != self._enabled:
                    self._enabled = enabled
                    changed = True
            if speed is not None:
                try:
                    speed = float(speed)
                except Exception:
                    speed = self._speed
                if speed < 0.1:
                    speed = 0.1
                if speed > 50:
                    speed = 50.0
                if abs(speed - self._speed) > 1e-9:
                    self._speed = speed
                    changed = True
            if changed:
                self._version += 1
            return SpeedState(self._enabled, self._speed, self._version)
