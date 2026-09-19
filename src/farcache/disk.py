"""基于 :mod:`diskcache` 的 SQLite 缓存后端。"""

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
    """对 :class:`diskcache.Cache` 的适配器。

    到达这一层的键已经是摘要，因此 diskcache 只需存储普通字符串，
    不需要再自己 pickle 一次键。
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
    """将函数结果缓存到 :mod:`diskcache` 存储中。

    Args:
        cache_key: 参数名、参数名序列，或 ``None`` 表示以全部参数为键。
        cache_dir: 缓存目录；为 ``None`` 时由函数身份派生。相对路径在
            装饰时解析一次。
        is_cache: 用于逐次调用开关缓存的参数名。
        expire: 条目存活时间（秒）；``None`` 表示永不过期。
        size_limit: 已存字节数上限，由 diskcache 自身的淘汰策略保证。
        settings: 透传给 :class:`diskcache.Cache` 的其余关键字参数
            （``eviction_policy``、``cull_limit``、``tag_index`` 等）。
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
            # 现算而不存到 self 上：同一个装饰器实例可能应用到多个函数，
            # 每个函数都需要自己的目录。
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
    """:class:`DiskCache` 的便捷工厂函数。

    Args:
        cache_key: 参数名、参数名序列，或 ``None`` 表示以全部参数为键。
        cache_dir: 缓存目录；为 ``None`` 时由函数身份派生。
        is_cache: 用于逐次调用开关缓存的参数名。
        expire: 条目存活时间（秒）；``None`` 表示永不过期。
        size_limit: 已存字节数上限。
        settings: 透传给 :class:`diskcache.Cache` 的其余关键字参数。

    Returns:
        新建的 :class:`DiskCache` 实例。
    """
    return DiskCache(
        cache_key=cache_key,
        cache_dir=cache_dir,
        is_cache=is_cache,
        expire=expire,
        size_limit=size_limit,
        **settings,
    )
