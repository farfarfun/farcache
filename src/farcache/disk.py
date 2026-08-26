"""SQLite-backed function cache, built on :mod:`diskcache`."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from hashlib import sha256
from typing import Any

from diskcache import Cache

from ._base import MISSING, CacheStore, FunctionCache
from ._utils import namespace_of

__all__ = ["DiskCache", "DiskStore", "disk_cache"]

_DEFAULT_ROOT = ".disk_cache"


class DiskStore(CacheStore):
    """Adapter over :class:`diskcache.Cache`.

    Keys reaching this layer are already digests, so diskcache stores plain
    strings and never has to pickle a key itself.
    """

    def __init__(
        self,
        directory: str,
        expire: float | None,
        **settings: Any,
    ) -> None:
        self.directory = directory
        self.expire = expire
        self._cache = Cache(directory, **settings)

    def get(self, digest: str) -> Any:
        return self._cache.get(digest, default=MISSING)

    def set(self, digest: str, value: Any) -> None:
        self._cache.set(digest, value, expire=self.expire)

    def delete(self, digest: str) -> bool:
        return bool(self._cache.delete(digest))

    def clear(self) -> int:
        return int(self._cache.clear())

    def prune(self) -> int:
        return int(self._cache.expire())

    def close(self) -> None:
        self._cache.close()


class DiskCache(FunctionCache):
    """Cache function results in a :mod:`diskcache` store.

    Args:
        cache_key: Parameter name, sequence of parameter names, or ``None`` to
            key on every argument.
        cache_dir: Directory for the cache. Derived from the function's identity
            when ``None``. Relative paths are resolved once, at decoration time.
        is_cache: Name of a parameter that toggles caching per call.
        expire: Entry lifetime in seconds; ``None`` means never expire.
        size_limit: Cap on total stored bytes, enforced by diskcache's own
            eviction policy.
        settings: Further keyword arguments forwarded to :class:`diskcache.Cache`
            (``eviction_policy``, ``cull_limit``, ``tag_index``, ...).
    """

    def __init__(
        self,
        cache_key: str | Iterable[str] | None = None,
        cache_dir: str | None = None,
        is_cache: str = "cache",
        expire: float | None = 60 * 60 * 24,
        size_limit: int | None = None,
        **settings: Any,
    ) -> None:
        super().__init__(cache_key=cache_key, is_cache=is_cache)
        self.cache_dir = cache_dir
        self.expire = expire
        if size_limit is not None:
            settings["size_limit"] = size_limit
        self.settings = settings

    def _prepare(self, func: Callable[..., Any]) -> str:
        directory = self.cache_dir
        if directory is None:
            # Derived, not stored on self: one decorator instance may be applied
            # to several functions, and each needs its own directory.
            uid = sha256(namespace_of(func).encode("utf-8")).hexdigest()[:16]
            name = getattr(func, "__name__", "func")
            directory = os.path.join(_DEFAULT_ROOT, f"{uid}-{name}")
        return os.path.abspath(directory)

    def _create_store(self, prepared: str) -> CacheStore:
        return DiskStore(prepared, self.expire, **self.settings)


def disk_cache(
    cache_key: str | Iterable[str] | None = None,
    cache_dir: str | None = None,
    is_cache: str = "cache",
    expire: float | None = 60 * 60 * 24,
    size_limit: int | None = None,
    **settings: Any,
) -> DiskCache:
    """Convenience factory for :class:`DiskCache`."""
    return DiskCache(
        cache_key=cache_key,
        cache_dir=cache_dir,
        is_cache=is_cache,
        expire=expire,
        size_limit=size_limit,
        **settings,
    )
