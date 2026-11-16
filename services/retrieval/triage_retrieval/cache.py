"""Small process-local TTL/LRU cache for deterministic corpus reads."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from typing import Any

from prometheus_client import Counter, Gauge

CACHE_ACCESS = Counter(
    "retrieval_cache_access_total", "Corpus response cache access", ["operation", "result"]
)
CACHE_ENTRIES = Gauge("retrieval_cache_entries", "Current live retrieval cache entries")


class TTLCache:
    def __init__(self, max_entries: int = 2048):
        self.max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str, operation: str):
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                CACHE_ACCESS.labels(operation, "miss").inc()
                return None
            expires_at, value = entry
            if expires_at <= now:
                del self._entries[key]
                CACHE_ENTRIES.set(len(self._entries))
                CACHE_ACCESS.labels(operation, "miss").inc()
                return None
            self._entries.move_to_end(key)
            CACHE_ACCESS.labels(operation, "hit").inc()
            return copy.deepcopy(value)

    def put(self, key: str, value: Any, ttl_seconds: float):
        if ttl_seconds <= 0:
            return
        now = time.monotonic()
        with self._lock:
            self._entries[key] = (now + ttl_seconds, copy.deepcopy(value))
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            CACHE_ENTRIES.set(len(self._entries))


def cache_key(operation: str, corpus_version: str, payload: dict) -> str:
    stable = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    raw = f"{operation}\0{corpus_version}\0{stable}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


TTL_SECONDS = max(0.0, float(os.getenv("RETRIEVAL_CACHE_TTL_SECONDS", "60")))
MAX_ENTRIES = max(1, int(os.getenv("RETRIEVAL_CACHE_MAX_ENTRIES", "2048")))
response_cache = TTLCache(MAX_ENTRIES)
