"""In-process metrics for `GET /api/metrics` (thread-safe, bounded memory)."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in [0, 100]); 0 for empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(q / 100 * len(ordered) + 0.5))
    return ordered[min(rank, len(ordered)) - 1]


class Metrics:
    """Rolling window of chat requests + counters."""

    def __init__(self, window: int = 1000) -> None:
        self._lock = threading.Lock()
        self._latency: deque[dict[str, float]] = deque(maxlen=window)
        self.started = time.time()
        self.requests = 0
        self.errors = 0
        self.abstained = 0
        self.degraded = 0
        self.cached = 0
        self.cost_usd = 0.0
        self.input_tokens = 0
        self.output_tokens = 0

    def record(self, result: dict[str, Any]) -> None:
        """Record one completed chat result."""
        usage = result.get("usage", {})
        with self._lock:
            self.requests += 1
            self.abstained += int(not result.get("answerable", False) and not result.get("degraded", False))
            self.degraded += int(bool(result.get("degraded")))
            self.cached += int(bool(result.get("cached")))
            self.cost_usd += float(usage.get("cost_usd", 0.0))
            self.input_tokens += int(usage.get("input_tokens", 0))
            self.output_tokens += int(usage.get("output_tokens", 0))
            self._latency.append({k: float(v) for k, v in usage.get("latency_ms", {}).items()})

    def record_error(self) -> None:
        """Count a failed request."""
        with self._lock:
            self.requests += 1
            self.errors += 1

    def snapshot(self, caches: dict[str, Any]) -> dict[str, Any]:
        """Aggregates for the API."""
        with self._lock:
            stages = sorted({k for row in self._latency for k in row})
            latency = {
                stage: {
                    "p50": round(percentile([r[stage] for r in self._latency if stage in r], 50), 1),
                    "p95": round(percentile([r[stage] for r in self._latency if stage in r], 95), 1),
                }
                for stage in stages
            }
            n = self.requests or 1
            return {
                "uptime_s": round(time.time() - self.started, 1),
                "requests": self.requests,
                "error_rate": round(self.errors / n, 4),
                "abstain_rate": round(self.abstained / n, 4),
                "degraded_rate": round(self.degraded / n, 4),
                "answer_cache_hit_rate": round(self.cached / n, 4),
                "latency_ms": latency,
                "tokens": {"input": self.input_tokens, "output": self.output_tokens},
                "cost_usd_total": round(self.cost_usd, 6),
                "cost_usd_per_request": round(self.cost_usd / n, 6),
                "caches": {
                    name: {
                        "hit_rate": round(cache.hit_rate, 4),
                        "hits": cache.hits,
                        "misses": cache.misses,
                        "size": len(cache),
                    }
                    for name, cache in caches.items()
                },
            }
