"""Atomic Redis sliding-window rate limiter keyed by client/tenant ID."""
from __future__ import annotations

import hashlib
import uuid

import redis

from shared.config import get_settings

_client = None
WINDOW_MS = 60_000

_SLIDING_WINDOW = """
local now = redis.call('TIME')
local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms - window_ms)
local used = redis.call('ZCARD', KEYS[1])
if used >= limit then
  redis.call('PEXPIRE', KEYS[1], window_ms + 1000)
  return 0
end
redis.call('ZADD', KEYS[1], now_ms, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window_ms + 1000)
return 1
"""


class RateLimitBackendUnavailable(RuntimeError):
    """Redis is unavailable, so the API cannot enforce its configured quota."""


def _redis():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            health_check_interval=30,
        )
    return _client


def allow(client_id: str) -> bool:
    """Allow only when fewer than N requests occurred in the prior 60 seconds.

    A Lua script makes prune/count/insert atomic across API replicas. The client ID
    is hashed before entering a Redis key; each accepted request has a unique member.
    """
    limit = get_settings().rate_limit_per_minute
    if limit <= 0:
        return True
    digest = hashlib.sha256((client_id or "unknown").encode("utf-8")).hexdigest()[:32]
    key = f"ratelimit:sliding:{digest}"
    try:
        result = _redis().eval(
            _SLIDING_WINDOW, 1, key, WINDOW_MS, limit, f"{uuid.uuid4().hex}"
        )
        return bool(result)
    except redis.exceptions.RedisError as exc:
        raise RateLimitBackendUnavailable("rate-limit store unavailable") from exc
