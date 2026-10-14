import threading

class WindowPosController:
    def __init__(self):
        self._lock = threading.Lock()
        self._version = 0

    def request(self) -> int:
        with self._lock:
            self._version += 1
            return self._version

    def get_version(self) -> int:
        with self._lock:
            return self._version

    def reset(self):
        with self._lock:
            self._version = 0