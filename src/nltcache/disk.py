import os
from functools import wraps
from hashlib import md5
from typing import Any, Callable, Optional

from diskcache import Cache
from nltlog import getLogger

from ._utils import ensure_gitignore, normalize_args

logger = getLogger("funcache")

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
        cache_dir: Optional[str] = None,
        is_cache: str = "cache",
        expire: int = 60 * 60 * 24,
    ) -> None:
        self.cache_key = cache_key
        self.cache_dir = cache_dir
        self.is_cache = is_cache
        self.expire = expire
        self._cache: Optional[Cache] = None

    def _init_cache(self, func: Callable) -> Cache:
        if self._cache is not None:
            return self._cache

        if self.cache_dir is None:
            uid = md5(func.__code__.co_filename.encode("utf-8")).hexdigest()
            self.cache_dir = os.path.join(".disk_cache", f"{uid}-{func.__name__}")

        self._cache = Cache(self.cache_dir)
        ensure_gitignore(self.cache_dir)

        logger.success(
            f"init func {func.__name__} success. with cache_dir: {self.cache_dir}"
        )
        return self._cache

    def __call__(self, func: Callable) -> Callable:
        cache = self._init_cache(func)

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            merged = normalize_args(func, args, kwargs)

            cache_key = merged.get(self.cache_key, "")
            is_cache = merged.get(self.is_cache, True) and cache_key is not None

            if not is_cache:
                return func(**merged)

            cached_result = cache.get(cache_key)
            if cached_result is not None:
                logger.debug(
                    f"Cache hit for function '{func.__name__}' with key: {cache_key}"
                )
                return cached_result

            result = func(**merged)
            cache.set(cache_key, result, expire=self.expire)
            logger.debug(
                f"Cache data for function '{func.__name__}' with key: {cache_key}"
            )
            return result

        return wrapper


def disk_cache(
    cache_key: str,
    cache_dir: Optional[str] = None,
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
