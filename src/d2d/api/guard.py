"""Copilot guard: protects the Mac mini's GPU when the live beta is reachable from the internet.

- Per-account quota: at most ``per_user_per_hour`` questions in any rolling hour (owner exempt).
- Concurrency: ``max_concurrent`` answers run at once; ``max_waiting`` more may queue; anything
  beyond that is refused immediately with "busy" instead of piling up requests.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from d2d.config import load_toml


@dataclass(frozen=True)
class ChatLimits:
    per_user_per_hour: int = 30
    max_concurrent: int = 1
    max_waiting: int = 2
    owner_exempt: bool = True


def load_limits(path: Path) -> ChatLimits:
    raw = load_toml(path).get("limits", {})
    return ChatLimits(
        per_user_per_hour=int(raw.get("per_user_per_hour", 30)),
        max_concurrent=max(1, int(raw.get("max_concurrent", 1))),
        max_waiting=max(0, int(raw.get("max_waiting", 2))),
        owner_exempt=bool(raw.get("owner_exempt", True)),
    )


class GuardRefused(Exception):
    def __init__(self, reason: str, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.reason = reason
        self.retry_after = retry_after


class ChatGuard:
    def __init__(self, limits: ChatLimits) -> None:
        self.limits = limits
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(limits.max_concurrent)
        self._admitted = 0  # running + waiting
        self._asked: dict[str, deque[float]] = defaultdict(deque)

    def check_quota(self, key: str, exempt: bool, now: float | None = None) -> None:
        if exempt and self.limits.owner_exempt:
            return
        t = now if now is not None else time.monotonic()
        with self._lock:
            q = self._asked[key]
            while q and q[0] <= t - 3600:
                q.popleft()
            if len(q) >= self.limits.per_user_per_hour:
                retry = int(q[0] + 3600 - t) + 1
                raise GuardRefused(
                    "quota",
                    f"question limit reached ({self.limits.per_user_per_hour} per hour); "
                    f"try again in {max(1, retry // 60)} min",
                    retry,
                )
            q.append(t)

    def __enter__(self) -> ChatGuard:
        with self._lock:
            if self._admitted >= self.limits.max_concurrent + self.limits.max_waiting:
                raise GuardRefused(
                    "busy", "the Copilot is busy answering others; try again shortly", 20
                )
            self._admitted += 1
        self._slots.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self._slots.release()
        with self._lock:
            self._admitted -= 1
