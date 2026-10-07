"""Thread-safe TTL + LRU cache with hit/miss metrics (retrieval, answers, query embeddings, sessions)."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Generic, TypeVar

V = TypeVar("V")


class TTLLRUCache(Generic[V]):
    """Bounded cache: entries expire after `ttl_s`; least-recently-used evicted beyond `max_items`."""

    def __init__(self, max_items: int, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_items = max_items
        self.ttl_s = ttl_s
        self._clock = clock
        self._data: OrderedDict[str, tuple[float, V]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> V | None:
        """Value or None (expired entries are dropped)."""
        with self._lock:
            item = self._data.get(key)
            if item is None or item[0] < self._clock():
                if item is not None:
                    del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return item[1]

    def set(self, key: str, value: V) -> None:
        """Insert/refresh."""
        with self._lock:
            self._data[key] = (self._clock() + self.ttl_s, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def clear(self) -> None:
        """Remove everything (metrics kept)."""
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    @property
    def hit_rate(self) -> float:
        """hits / lookups (0 when unused)."""
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
