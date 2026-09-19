"""内存缓存，基于 :mod:`cachebox` 的淘汰策略实现。

这里的每个装饰器都支持裸用和带参数调用两种形式::

    @lru_cache
    def f(x): ...

    @lru_cache(maxsize=500)
    def g(x): ...

包装函数会把底层策略暴露为 ``f.cache``，因此 ``f.cache.clear()`` 可以清空缓存，
``len(f.cache)`` 可以查看当前大小。
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

#: 装饰器裸用（不带参数列表）时使用的默认值。
DEFAULT_MAXSIZE = 1000
DEFAULT_TTL = 60

_MaybeFunc = int | Callable[..., Any]
_MISS = object()


def _apply(policy: Any, maxsize: _MaybeFunc) -> Any:
    """返回一个装饰器；若裸用则直接返回被装饰后的函数。"""
    decorator = cached(policy)
    return decorator(maxsize) if callable(maxsize) else decorator


def cache(func: F, /) -> F:
    """LRU 缓存，默认 maxsize 为 1000，裸用（不带参数）。"""
    return cached(LRUCache(maxsize=DEFAULT_MAXSIZE))(func)


def lru_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE) -> Any:
    """LRUCache：移除缓存中自上次访问以来时间最长的元素。"""
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    return _apply(LRUCache(maxsize=size), maxsize)


def lfu_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE) -> Any:
    """LFUCache：移除缓存中访问次数最少的元素，不论其访问时间。"""
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    return _apply(LFUCache(maxsize=size), maxsize)


def fifo_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE) -> Any:
    """FIFOCache：移除在缓存中停留时间最长的元素。"""
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    return _apply(FIFOCache(maxsize=size), maxsize)


def rr_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE) -> Any:
    """RRCache：在必要时随机选择一个元素进行移除，以腾出空间。"""
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    return _apply(RRCache(maxsize=size), maxsize)


def ttl_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE, ttl: float = DEFAULT_TTL) -> Any:
    """TTLCache：自动移除已过期的缓存元素。"""
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    return _apply(TTLCache(maxsize=size, ttl=ttl), maxsize)


def vttl_cache(maxsize: _MaybeFunc = DEFAULT_MAXSIZE, ttl: float = DEFAULT_TTL) -> Any:
    """VTTLCache：在访问时才惰性移除已过期的缓存元素。"""
    size = DEFAULT_MAXSIZE if callable(maxsize) else maxsize
    store: VTTLCache[Any, Any] = VTTLCache(maxsize=size)

    # VTTLCache 是按每个键单独过期的，所以存活时间要在插入时提供。
    # cachebox 自带的 `cached` 没有对应的钩子，因此这里用显式包装 --
    # 如果把 ttl 传给构造函数，只会作用于种子数据，导致这个装饰器
    # 之后存入的所有条目都悄悄变成永不过期。
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
