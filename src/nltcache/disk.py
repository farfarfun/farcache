from __future__ import annotations

import inspect
import os
from functools import wraps
from hashlib import sha256
from typing import Any, Callable

from diskcache import Cache

from ._utils import bind_args

_MISSING = object()

__all__ = ["DiskCache", "disk_cache"]


class DiskCache:
    """Decorator that caches function results using :class:`diskcache.Cache`.

    Args:
        cache_key: The name of the function parameter whose value is used as the cache key.
        cache_dir: Directory for the disk cache. Auto-generated from the function if *None*.
        is_cache: The name of a boolean parameter that controls whether caching is enabled.
        expire: Cache entry expiration time in seconds (default: 1 day).
    """

    def __init__(
        self,
        cache_key: str,
        cache_dir: str | None = None,
        is_cache: str = "cache",
        expire: int = 60 * 60 * 24,
    ) -> None:
        self.cache_key = cache_key
        self.cache_dir = cache_dir
        self.is_cache = is_cache
        self.expire = expire
        self._cache: Cache | None = None

    def _init_cache(self, func: Callable) -> Cache:
        if self._cache is not None:
            return self._cache

        if self.cache_dir is None:
            identity = f"{func.__module__}.{func.__qualname__}"
            uid = sha256(identity.encode("utf-8")).hexdigest()[:16]
            self.cache_dir = os.path.join(".disk_cache", f"{uid}-{func.__name__}")

        self._cache = Cache(self.cache_dir)
        return self._cache

    def __call__(self, func: Callable) -> Callable:
        signature = inspect.signature(func)
        if self.cache_key not in signature.parameters:
            raise ValueError(
                f"cache key {self.cache_key!r} is not a parameter of {func.__qualname__}"
            )
        namespace = f"{func.__module__}.{func.__qualname__}"

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = bind_args(signature, args, kwargs)

            cache_key = bound[self.cache_key]
            is_cache = bound.get(self.is_cache, True) and cache_key is not None

            if not is_cache:
                return func(*args, **kwargs)

            cache = self._init_cache(func)
            typed_key = (
                namespace,
                f"{type(cache_key).__module__}.{type(cache_key).__qualname__}",
                cache_key,
            )
            cached_result = cache.get(typed_key, default=_MISSING)
            if cached_result is not _MISSING:
                return cached_result

            result = func(*args, **kwargs)
            cache.set(typed_key, result, expire=self.expire)
            return result

        return wrapper


def disk_cache(
    cache_key: str,
    cache_dir: str | None = None,
    is_cache: str = "cache",
    expire: int = 60 * 60 * 24,
) -> DiskCache:
    """Convenience factory for :class:`DiskCache`."""
    return DiskCache(
        cache_key=cache_key,
        cache_dir=cache_dir,
        is_cache=is_cache,
        expire=expire,
    )
