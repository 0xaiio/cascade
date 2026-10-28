import time
from collections.abc import Callable


class TransferStats:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_downloaded: int | None = None
        self._last_seen_at: float | None = None
        self._bytes = 0
        self._seconds = 0.0

    def record(self, downloaded_bytes: int) -> None:
        now = self._clock()
        if downloaded_bytes < 0:
            return
        if self._last_downloaded is None or self._last_seen_at is None:
            self._last_downloaded = downloaded_bytes
            self._last_seen_at = now
            return
        if downloaded_bytes < self._last_downloaded:
            self._last_downloaded = downloaded_bytes
            self._last_seen_at = now
            return

        byte_delta = downloaded_bytes - self._last_downloaded
        second_delta = now - self._last_seen_at
        if byte_delta > 0 and second_delta > 0:
            self._bytes += byte_delta
            self._seconds += second_delta
        self._last_downloaded = downloaded_bytes
        self._last_seen_at = now

    def average_speed(self) -> float | None:
        if self._bytes <= 0 or self._seconds <= 0:
            return None
        return self._bytes / self._seconds
