"""Fixed-window rate limiter keyed by client id. Rate limiting is cost control for a multi-step agent."""
import time

import redis

from shared.config import get_settings

_client = None


def _redis():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url)
    return _client


def allow(client_id: str) -> bool:
    limit = get_settings().rate_limit_per_minute
    if limit <= 0:
        return True
    key = f"ratelimit:{client_id}:{int(time.time() // 60)}"
    r = _redis()
    count = r.incr(key)
    if count == 1:
        r.expire(key, 90)
    return count <= limit
