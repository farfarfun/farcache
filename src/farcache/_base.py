"""按参数键控的持久化缓存的共享机制。

``PickleCache`` 与 ``DiskCache`` 仅在字节落地位置上有差异，签名校验、键推导、
``is_cache`` 逃生舱、同步/异步包装以及内省 API 都放在这里。后端实现
:class:`CacheStore`。
"""

from __future__ import annotations

import functools
import inspect
import threading
from collections.abc import Callable, Iterable
from typing import (
    Any,
    ParamSpec,
    Protocol,
    TypeVar,
    cast,
)

from farlog import get_logger

from ._keys import UnstableKeyError, key_digest
from ._utils import bind_args, namespace_of

P = ParamSpec("P")
R = TypeVar("R")

__all__ = ["MISSING", "CacheStore", "CachedFunction", "FunctionCache"]

logger = get_logger("farcache")


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISSING>"

    def __bool__(self) -> bool:
        return False


#: 表示"不在缓存中"的哨兵值。``None`` 本身是可缓存的合法值，
#: 不能兼职当作未命中标记。
MISSING: Any = _Missing()


class CacheStore(Protocol):
    """单个被装饰函数的后端存储。"""

    def get(self, digest: str) -> Any:
        """返回已存储的值，或 :data:`MISSING`。"""

    def set(self, digest: str, value: Any) -> None: ...

    def delete(self, digest: str) -> bool:
        """删除一条记录；返回它此前是否存在。"""

    def clear(self) -> int:
        """删除所有记录；返回删除的数量。"""

    def prune(self) -> int:
        """删除已过期的记录；返回删除的数量。"""

    def close(self) -> None: ...


class CachedFunction(Protocol[P, R]):
    """一个被装饰的函数，附带挂在它身上的缓存控制 API。"""

    __wrapped__: Callable[P, R]

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R: ...

    def cache_key(self, *args: P.args, **kwargs: P.kwargs) -> str | None:
        """返回给定调用会使用的摘要；若该调用会绕过缓存则返回 ``None``。"""

    def cache_invalidate(self, *args: P.args, **kwargs: P.kwargs) -> bool:
        """删除给定调用对应的条目；返回它此前是否存在。"""

    def cache_clear(self) -> int: ...

    def cache_prune(self) -> int: ...

    def cache_close(self) -> None: ...


class _FunctionState:
    """单个被装饰函数的状态：惰性构建的存储，只创建一次。"""

    def __init__(self, owner: FunctionCache, prepared: Any) -> None:
        self._owner = owner
        self._prepared = prepared
        self._store: CacheStore | None = None
        self._lock = threading.Lock()
        self.warned = False

    def store(self) -> CacheStore:
        store = self._store
        if store is None:
            with self._lock:
                store = self._store
                if store is None:
                    store = self._store = self._owner._create_store(self._prepared)
        return store

    def close(self) -> None:
        with self._lock:
            store, self._store = self._store, None
        if store is not None:
            store.close()


def _normalize_key_names(
    cache_key: str | Iterable[str] | None,
) -> tuple[str, ...] | None:
    if cache_key is None:
        return None
    if isinstance(cache_key, str):
        return (cache_key,)
    names = tuple(cache_key)
    if not names:
        raise ValueError("cache_key must name at least one parameter, or be None")
    if not all(isinstance(name, str) for name in names):
        raise TypeError("cache_key must be a parameter name or a sequence of them")
    return names


def _reject_unsupported(func: Callable[..., Any]) -> None:
    if inspect.isasyncgenfunction(func) or inspect.isgeneratorfunction(func):
        raise TypeError(
            f"{namespace_of(func)} is a generator function; a generator is consumed "
            "on first use and cannot be stored. Return a list instead."
        )
    if not callable(func):
        raise TypeError(f"{func!r} is not callable")


class FunctionCache:
    """按函数部分参数键控的缓存的基类。

    Args:
        cache_key: 参数名、参数名序列，或 ``None`` 表示以全部参数为键。
        is_cache: 用于逐次调用开关缓存的参数名。
    """

    def __init__(
        self,
        cache_key: str | Iterable[str] | None = None,
        is_cache: str = "cache",
    ) -> None:
        self.cache_key = cache_key
        self.is_cache = is_cache
        self._key_names = _normalize_key_names(cache_key)
        self._states: list[_FunctionState] = []
        self._states_lock = threading.Lock()

    # -- 后端钩子 ----------------------------------------------------

    def _prepare(self, func: Callable[..., Any]) -> Any:
        """在装饰时解析每个函数的专属配置（不做 I/O）。"""
        return None

    def _create_store(self, prepared: Any) -> CacheStore:
        """打开后端存储。只在第一次被缓存调用时调用一次。"""
        raise NotImplementedError

    def _report(self, event: str, func: Callable[..., Any]) -> None:
        logger.debug("{} for {}", event, namespace_of(func))

    # -- 公开 API -------------------------------------------------------

    def close(self) -> None:
        """释放这个装饰器打开过的每一个存储。"""
        with self._states_lock:
            states = list(self._states)
        for state in states:
            state.close()

    def __enter__(self) -> FunctionCache:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __call__(self, func: Callable[P, R]) -> CachedFunction[P, R]:
        _reject_unsupported(func)
        signature = inspect.signature(func)
        key_names = self._key_names
        if key_names is not None:
            unknown = [n for n in key_names if n not in signature.parameters]
            if unknown:
                raise ValueError(
                    f"cache key {', '.join(map(repr, unknown))} is not a parameter "
                    f"of {namespace_of(func)}"
                )

        namespace = namespace_of(func)
        state = _FunctionState(self, self._prepare(func))
        with self._states_lock:
            self._states.append(state)

        def digest_for(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
            """返回本次调用的摘要；若本次调用应绕过缓存则返回 None。"""
            bound = bind_args(signature, args, kwargs)

            if not bound.get(self.is_cache, True):
                return None

            if key_names is None:
                # 切换是否缓存不应改变结果的存储位置。
                material: Any = {
                    name: value
                    for name, value in bound.items()
                    if name != self.is_cache
                }
            else:
                material = [bound[name] for name in key_names]
                # None 键是文档规定的"无键可用"逃生舱。
                if any(value is None for value in material):
                    return None

            try:
                return key_digest(namespace, material)
            except UnstableKeyError:
                if key_names is not None:
                    # 该参数是显式指定的；静默失败会把一次拼写错误
                    # 变成永久的缓存未命中。
                    raise
                if not state.warned:
                    state.warned = True
                    logger.opt(exception=True).warning(
                        "{}: arguments are not reproducibly hashable, caching "
                        "disabled for this function",
                        namespace,
                    )
                return None

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                digest = digest_for(args, kwargs)
                if digest is None:
                    return await func(*args, **kwargs)

                store = state.store()
                cached = store.get(digest)
                if cached is not MISSING:
                    self._report("Cache hit", func)
                    return cached

                result = await func(*args, **kwargs)
                store.set(digest, result)
                self._report("Cache store", func)
                return result

        else:

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                digest = digest_for(args, kwargs)
                if digest is None:
                    return func(*args, **kwargs)

                store = state.store()
                cached = store.get(digest)
                if cached is not MISSING:
                    self._report("Cache hit", func)
                    return cached

                result = func(*args, **kwargs)
                store.set(digest, result)
                self._report("Cache store", func)
                return result

        def cache_key(*args: Any, **kwargs: Any) -> str | None:
            return digest_for(args, kwargs)

        def cache_invalidate(*args: Any, **kwargs: Any) -> bool:
            digest = digest_for(args, kwargs)
            return False if digest is None else state.store().delete(digest)

        wrapper.cache_key = cache_key  # type: ignore[attr-defined]
        wrapper.cache_invalidate = cache_invalidate  # type: ignore[attr-defined]
        wrapper.cache_clear = lambda: state.store().clear()  # type: ignore[attr-defined]
        wrapper.cache_prune = lambda: state.store().prune()  # type: ignore[attr-defined]
        wrapper.cache_close = state.close  # type: ignore[attr-defined]
        return cast("CachedFunction[P, R]", wrapper)
