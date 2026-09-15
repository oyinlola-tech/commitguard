"""Fixed-window request rate limiting with bounded memory.

Used by the webhook endpoint (per client address) and the dashboard API (per
user or client address, per operation category). Limits are per process.
"""

import threading
import time
from collections.abc import Callable


class RequestRateLimiter:
    """Fixed-window request counter per client address (bounded memory)."""

    MAX_TRACKED = 10_000

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = per_minute
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}

    def allow(self, key: str) -> bool:
        window = int(self._clock() // 60)
        with self._lock:
            if len(self._windows) > self.MAX_TRACKED:
                self._windows.clear()
            start, count = self._windows.get(key, (window, 0))
            if start != window:
                start, count = window, 0
            count += 1
            self._windows[key] = (start, count)
            return count <= self._limit
