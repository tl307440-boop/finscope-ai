import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.exceptions import AppError


@dataclass
class LoginRateLimiter:
    max_failures: int
    window_seconds: int
    _failures: dict[str, list[datetime]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, username: str, now: datetime) -> None:
        with self._lock:
            if len(self._recent(username, now)) >= self.max_failures:
                raise AppError(
                    code="RATE_LIMITED",
                    message="尝试次数过多，请稍后再试",
                    status_code=429,
                )

    def record_failure(self, username: str, now: datetime) -> None:
        with self._lock:
            recent = self._recent(username, now)
            recent.append(now)
            self._failures[username] = recent

    def clear(self, username: str) -> None:
        with self._lock:
            self._failures.pop(username, None)

    def _recent(self, username: str, now: datetime) -> list[datetime]:
        window = timedelta(seconds=self.window_seconds)
        return [item for item in self._failures.get(username, []) if now - item <= window]
