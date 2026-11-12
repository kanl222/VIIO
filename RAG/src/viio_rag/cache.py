"""Redis caching module for VIIO RAG with graceful in-memory fallback."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    aioredis = None
    REDIS_AVAILABLE = False


class InMemoryCache:
    """Lightweight in-memory cache with TTL expiration for fallback when Redis is offline."""

    def __init__(self, max_items: int = 2000) -> None:
        self.max_items = max_items
        self._store: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, val = item
        if expires_at and time.monotonic() > expires_at:
            self._store.pop(key, None)
            return None
        return val

    def set(self, key: str, val: str, ttl: int | None = None) -> None:
        if len(self._store) >= self.max_items:
            # Drop oldest 20% of entries
            to_remove = list(self._store.keys())[: max(1, self.max_items // 5)]
            for k in to_remove:
                self._store.pop(k, None)
        expires_at = time.monotonic() + ttl if ttl else 0.0
        self._store[key] = (expires_at, val)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


class ViioRedisCache:
    """High-performance Redis cache with connection resilience and in-memory fallback."""

    def __init__(
        self,
        redis_url: str | None = "redis://localhost:6379/0",
        enabled: bool = True,
        default_ttl: int = 86400,
    ) -> None:
        self.redis_url = redis_url or "redis://localhost:6379/0"
        self.enabled = enabled and REDIS_AVAILABLE
        self.default_ttl = default_ttl
        self._client: Any = None
        self._fallback = InMemoryCache()
        self._redis_online: bool | None = None

    async def _get_client(self) -> Any:
        if not self.enabled or not REDIS_AVAILABLE:
            return None
        if self._client is None:
            try:
                self._client = aioredis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=2.0,
                    socket_timeout=2.0,
                )
                await self._client.ping()
                self._redis_online = True
                logger.info("Connected to Redis cache at %s", self.redis_url)
            except Exception as e:
                self._redis_online = False
                logger.warning("Redis cache unavailable at %s (%s). Falling back to in-memory cache.", self.redis_url, e)
                if self._client:
                    try:
                        await self._client.aclose()
                    except Exception:
                        pass
                    self._client = None
        return self._client

    async def ping(self) -> bool:
        """Check if Redis connection is alive."""
        try:
            client = await self._get_client()
            if client:
                await client.ping()
                self._redis_online = True
                return True
        except Exception:
            self._redis_online = False
        return False

    @property
    def is_redis_active(self) -> bool:
        return bool(self._redis_online)

    def _hash_key(self, prefix: str, raw_key: str) -> str:
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]
        return f"viio:{prefix}:{digest}"

    async def get_json(self, key: str) -> Any | None:
        """Retrieve and parse JSON from Redis or in-memory fallback."""
        try:
            client = await self._get_client()
            if client:
                val = await client.get(key)
                if val:
                    return json.loads(val)
                return None
        except Exception as e:
            logger.debug("Redis get_json error for %s: %s", key, e)
            self._redis_online = False

        raw = self._fallback.get(key)
        return json.loads(raw) if raw else None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Store JSON value with TTL in Redis or in-memory fallback."""
        serialized = json.dumps(value, ensure_ascii=False)
        expiry = ttl or self.default_ttl
        saved = False
        try:
            client = await self._get_client()
            if client:
                await client.set(key, serialized, ex=expiry)
                saved = True
        except Exception as e:
            logger.debug("Redis set_json error for %s: %s", key, e)
            self._redis_online = False

        self._fallback.set(key, serialized, ttl=expiry)
        return saved or True

    async def get_embedding(self, text: str, model: str) -> list[float] | None:
        """Retrieve cached embedding vector for a given text and model."""
        key = self._hash_key("emb", f"{model}:{text.strip()}")
        return await self.get_json(key)

    async def set_embedding(self, text: str, model: str, vector: list[float], ttl: int = 604800) -> bool:
        """Cache embedding vector (default TTL 7 days)."""
        key = self._hash_key("emb", f"{model}:{text.strip()}")
        return await self.set_json(key, vector, ttl=ttl)

    async def get_cached_retrieval(self, query: str, context_repr: str, k: int) -> list[dict] | None:
        """Retrieve cached retrieval result documents."""
        key = self._hash_key("retrieval", f"{query.strip().lower()}:{context_repr}:{k}")
        return await self.get_json(key)

    async def set_cached_retrieval(self, query: str, context_repr: str, k: int, docs: list[dict], ttl: int = 3600) -> bool:
        """Cache retrieval results (default TTL 1 hour)."""
        key = self._hash_key("retrieval", f"{query.strip().lower()}:{context_repr}:{k}")
        return await self.set_json(key, docs, ttl=ttl)

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None


_global_cache: ViioRedisCache | None = None


def get_cache(settings: Any = None) -> ViioRedisCache:
    """Return the global ViioRedisCache instance."""
    global _global_cache
    if _global_cache is None:
        redis_url = getattr(settings, "redis_url", None)
        enabled = getattr(settings, "redis_enabled", True)
        ttl = getattr(settings, "redis_cache_ttl", 86400)
        _global_cache = ViioRedisCache(redis_url=redis_url, enabled=enabled, default_ttl=ttl)
    return _global_cache
