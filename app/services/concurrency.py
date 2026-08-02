from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.errors import ConcurrencyLimitReached

logger = logging.getLogger("linkparse.concurrency")

ACQUIRE_SCRIPT = """
local now = tonumber(ARGV[1])
local expires = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) < limit then
  redis.call('ZADD', KEYS[1], expires, ARGV[4])
  redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[5]))
  return 1
end
return 0
"""

RELEASE_SCRIPT = "return redis.call('ZREM', KEYS[1], ARGV[1])"
RENEW_SCRIPT = """
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  redis.call('ZADD', KEYS[1], 'XX', tonumber(ARGV[2]), ARGV[1])
  redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]))
  return 1
end
return 0
"""

class ConcurrencyLimiter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.redis = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=2,
            decode_responses=True,
        )

    def limit_for(self, engine: str) -> int:
        if engine == "rapidocr":
            return self.settings.ocr_max_concurrency
        if engine == "opendataloader":
            return self.settings.opendataloader_max_concurrency
        raise ValueError(f"Unknown concurrency engine: {engine}")

    def _key(self, engine: str) -> str:
        return f"linkparse:concurrency:{engine}"

    @contextmanager
    def slot(self, engine: str) -> Iterator[None]:
        limit = self.limit_for(engine)
        member = uuid.uuid4().hex
        # Keep the original lease valid for the full bounded parse duration even if a
        # transient Redis outage prevents heartbeats from extending it.
        lease_ms = max(90_000, (self.settings.task_time_limit_seconds + 60) * 1000)
        deadline = time.monotonic() + self.settings.concurrency_wait_seconds
        redis_acquired = False
        renew_stop = threading.Event()
        renew_thread = None

        while True:
            now_ms = int(time.time() * 1000)
            try:
                redis_acquired = bool(
                    self.redis.eval(
                        ACQUIRE_SCRIPT,
                        1,
                        self._key(engine),
                        now_ms,
                        now_ms + lease_ms,
                        limit,
                        member,
                        lease_ms,
                    )
                )
            except RedisError as exc:
                logger.warning("concurrency_redis_unavailable engine=%s error=%s", engine, exc)
                raise ConcurrencyLimitReached(engine) from exc
            if redis_acquired or time.monotonic() >= deadline:
                break
            time.sleep(0.05)

        if not redis_acquired:
            raise ConcurrencyLimitReached(engine)

        if redis_acquired:
            renew_thread = threading.Thread(
                target=self._renew_slot,
                args=(engine, member, lease_ms, renew_stop),
                name=f"linkparse-slot-{engine}",
                daemon=True,
            )
            renew_thread.start()

        try:
            yield
        finally:
            renew_stop.set()
            if renew_thread is not None:
                renew_thread.join(timeout=1)
            if redis_acquired:
                try:
                    self.redis.eval(RELEASE_SCRIPT, 1, self._key(engine), member)
                except RedisError:
                    logger.warning("concurrency_slot_release_failed engine=%s", engine)

    def _renew_slot(
        self,
        engine: str,
        member: str,
        lease_ms: int,
        stop: threading.Event,
    ) -> None:
        while not stop.wait(30):
            try:
                expires_at = int(time.time() * 1000) + lease_ms
                renewed = self.redis.eval(
                    RENEW_SCRIPT,
                    1,
                    self._key(engine),
                    member,
                    expires_at,
                    lease_ms,
                )
                if not renewed:
                    logger.warning("concurrency_slot_lost engine=%s", engine)
                    return
            except RedisError:
                logger.warning("concurrency_slot_renew_failed engine=%s", engine)
                return

    def describe(self) -> dict:
        now_ms = int(time.time() * 1000)
        result = {"available": True, "engines": {}}
        try:
            for engine in ("rapidocr", "opendataloader"):
                key = self._key(engine)
                self.redis.zremrangebyscore(key, "-inf", now_ms)
                result["engines"][engine] = {
                    "active": self.redis.zcard(key),
                    "limit": self.limit_for(engine),
                }
        except RedisError:
            result["available"] = False
            result["engines"] = {
                engine: {"active": None, "limit": self.limit_for(engine)}
                for engine in ("rapidocr", "opendataloader")
            }
        return result
