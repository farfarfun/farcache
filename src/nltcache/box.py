"""In-memory caches, backed by :mod:`cachebox` eviction policies.

Every decorator here works both bare and called::

    @lru_cache
    def f(x): ...

    @lru_cache(maxsize=500)
    def g(x): ...

The wrapper exposes the underlying policy as ``f.cache``, so ``f.cache.clear()``
empties it and ``len(f.cache)`` reports its size.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from cachebox import (
    FIFOCache,
    LFUCache,
    LRUCache,
    RRCache,
    TTLCache,
    VTTLCache,
    cached,
    make_key,
)

__all__ = [
    "cache",
    "fifo_cache",
    "lfu_cache",
    "lru_cache",
    "rr_cache",
    "ttl_cache",
    "vttl_cache",
]

F = TypeVar("F", bound=Callable[..., Any])

#: Used when a decorator is applied bare, i.e. without an argument list.
DEFAULT_MAXSIZE = 1000
DEFAULT_TTL = 60

_MaybeFunc = int | Callable[..., Any]
_MISS = object()


def _apply(policy: Any, maxsize: _MaybeFunc) -> Any:
    """Return a decorator, or the decorated function when used bare."""
    decorator = cached(policy)
    return decorator(maxsize) if callable(maxsize) else decorator


def cache(func: F, /) -> F:
    """LRU cache with a default maxsize of 1000, applied without arguments."""
    return cached(LRUCache(maxsize=DEFAULT_MAXSIZE))(func)


def lru_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE) -> Any:
    """LRUCache: evicts the least recently used entry.

    LRUCache：移除缓存中自上次访问以来时间最长的元素。
    """
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    return _apply(LRUCache(maxsize=size), maxsize)


def lfu_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE) -> Any:
    """LFUCache: evicts the least frequently used entry.

    LFUCache：移除缓存中访问次数最少的元素，不论其访问时间。
    """
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    return _apply(LFUCache(maxsize=size), maxsize)


def fifo_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE) -> Any:
    """FIFOCache: evicts the oldest entry.

    FIFOCache：移除在缓存中停留时间最长的元素。
    """
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    return _apply(FIFOCache(maxsize=size), maxsize)


def rr_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE) -> Any:
    """RRCache: evicts a random entry when space is needed.

    RRCache: 在必要时随机选择一个元素进行移除，以腾出空间。
    """
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    return _apply(RRCache(maxsize=size), maxsize)


def ttl_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE, ttl: float = DEFAULT_TTL) -> Any:
    """TTLCache: evicts expired entries eagerly.

    TTLCache：自动移除已过期的缓存元素。
    """
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    return _apply(TTLCache(maxsize=size, ttl=ttl), maxsize)


def vttl_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE, ttl: float = DEFAULT_TTL) -> Any:
    """VTTLCache: evicts expired entries lazily, on access.

    VTTLCache: 在访问时才惰性移除已过期的缓存元素。
    """
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    store: VTTLCache[Any, Any] = VTTLCache(maxsize=size)

    # VTTLCache expires per key, so the lifetime is supplied at insert time.
    # cachebox's own `cached` has no hook for that, hence the explicit wrapper --
    # passing ttl to the constructor would only apply to seed items, silently
    # leaving everything this decorator stores immortal.
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                key = make_key(args, kwargs)
                result = store.get(key, _MISS)
                if result is not _MISS:
                    return result
                result = await func(*args, **kwargs)
                store.insert(key, result, ttl)
                return result

        else:

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                key = make_key(args, kwargs)
                result = store.get(key, _MISS)
                if result is not _MISS:
                    return result
                result = func(*args, **kwargs)
                store.insert(key, result, ttl)
                return result

        wrapper.cache = store  # type: ignore[attr-defined]
        wrapper.cache_clear = store.clear  # type: ignore[attr-defined]
        return wrapper

    return decorator(maxsize) if callable(maxsize) else decorator
