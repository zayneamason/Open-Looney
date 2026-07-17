"""Simple LRU cache with a dedup time window, used by LunaFM channels."""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Optional


class LRUDedupCache:
    """
    Bounded LRU keyed by string, with a dedup window.

    `seen_recently(key)` returns True if the key was touched within
    `dedup_window_s`. `touch(key, value)` records access.

    Also exposes a simple rolling counter for rate-limiting emissions
    per hour via `increment_hour()` / `hourly_count()`.
    """

    def __init__(self, max_items: int = 50, dedup_window_s: int = 300):
        self._max_items = max_items
        self._dedup_window_s = dedup_window_s
        self._items: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._emission_times: list[float] = []

    def seen_recently(self, key: str) -> bool:
        entry = self._items.get(key)
        if entry is None:
            return False
        ts, _ = entry
        return (time.time() - ts) < self._dedup_window_s

    def touch(self, key: str, value: Any = None) -> None:
        self._items[key] = (time.time(), value)
        self._items.move_to_end(key)
        while len(self._items) > self._max_items:
            self._items.popitem(last=False)

    def get(self, key: str) -> Optional[Any]:
        entry = self._items.get(key)
        return entry[1] if entry else None

    def increment_hour(self) -> None:
        now = time.time()
        self._emission_times.append(now)
        cutoff = now - 3600
        self._emission_times = [t for t in self._emission_times if t >= cutoff]

    def hourly_count(self) -> int:
        now = time.time()
        cutoff = now - 3600
        self._emission_times = [t for t in self._emission_times if t >= cutoff]
        return len(self._emission_times)
