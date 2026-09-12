"""
Simple in-memory sliding-window rate limiter, keyed per Telegram user.

This is intentionally lightweight (no Redis dependency) - good enough to stop
button-mashing / spam-command abuse. If you run multiple bot processes behind
a load balancer you'd want to move this to a shared store, but a single
polling/webhook process (the normal deployment for this bot) is fine with
in-memory state.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from app.config import settings


class RateLimiter:
    def __init__(self, max_actions: int, window_seconds: int) -> None:
        self.max_actions = max_actions
        self.window_seconds = window_seconds
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        now = time.monotonic()
        q = self._hits[user_id]
        cutoff = now - self.window_seconds
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.max_actions:
            return False
        q.append(now)
        return True


rate_limiter = RateLimiter(settings.rate_limit_actions, settings.rate_limit_window)


class DebounceGuard:
    """
    Prevents the exact same callback (same user + same callback data) from
    being processed twice within a very short window - protects against
    Telegram's double-tap / double-delivery of callback queries.
    """

    def __init__(self, window_seconds: float = 1.0) -> None:
        self.window_seconds = window_seconds
        self._last: dict[tuple[int, str], float] = {}

    def allow(self, user_id: int, key: str) -> bool:
        now = time.monotonic()
        last = self._last.get((user_id, key))
        if last is not None and (now - last) < self.window_seconds:
            return False
        self._last[(user_id, key)] = now
        return True


debounce_guard = DebounceGuard()
