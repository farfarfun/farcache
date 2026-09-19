"""基于 pickle 文件的函数缓存。"""

from __future__ import annotations

import contextlib
import os
import pickle
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from functools import cached_property
from typing import Any

from farlog import get_logger

from ._base import MISSING, CacheStore, FunctionCache
from ._keys import PICKLE_PROTOCOL
from ._utils import namespace_of

__all__ = ["PickleCache", "PickleStore", "cached_property", "pkl_cache"]

logger = get_logger("farcache")

_SUFFIX = ".pkl"

#: 按 max_entries 裁剪需要扫描目录，摊到多次写入里做，而不是每次写入都付出这个代价。
_TRIM_INTERVAL = 256


class PickleStore(CacheStore):
    """每个条目一个 pickle 文件，原子写入。

    文件按摘要前缀分片到 256 个子目录，避免大缓存退化成单个巨大目录。
    每个文件保存两个 pickle 对象：先是过期时间戳，再是值，这样检查是否
    过期时不需要反序列化整个值。
    """

    def __init__(
        self,
        directory: str,
        expire: float | None = None,
        max_entries: int | None = None,
    ) -> None:
        self.directory = directory
        self.expire = expire
        self.max_entries = max_entries
        self._writes = 0
        self._lock = threading.Lock()

    def _path(self, digest: str) -> str:
        return os.path.join(self.directory, digest[:2], digest + _SUFFIX)

    @staticmethod
    def _unlink(path: str) -> bool:
        try:
            os.unlink(path)
            return True
        except OSError:
            return False

    def get(self, digest: str) -> Any:
        path = self._path(digest)
        try:
            with open(path, "rb") as handle:
                expires_at = pickle.load(handle)
                if expires_at is not None and expires_at <= time.time():
                    self._unlink(path)
                    return MISSING
                return pickle.load(handle)
        except FileNotFoundError:
            return MISSING
        except Exception:
            # 文件被截断、由旧版本半途写入，或引用了后来被改名的类：
            # 直接丢弃并重新计算。
            self._unlink(path)
            return MISSING

    def set(self, digest: str, value: Any) -> None:
        path = self._path(digest)
        shard = os.path.dirname(path)
        os.makedirs(shard, exist_ok=True)
        expires_at = None if self.expire is None else time.time() + self.expire

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=shard, delete=False
            ) as handle:
                temp_path = handle.name
                pickle.dump(expires_at, handle, protocol=PICKLE_PROTOCOL)
                pickle.dump(value, handle, protocol=PICKLE_PROTOCOL)
            os.replace(temp_path, path)
            temp_path = None  # 已被 rename 消费
        finally:
            if temp_path is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temp_path)

        self._maybe_trim()

    def delete(self, digest: str) -> bool:
        return self._unlink(self._path(digest))

    def clear(self) -> int:
        removed = 0
        for path in self._entries():
            removed += self._unlink(path)
        self._drop_empty_shards()
        return removed

    def prune(self) -> int:
        """丢弃已过期条目，再按 max_entries 从最旧的开始裁剪。"""
        now = time.time()
        live: list[tuple[float, str]] = []
        removed = 0

        for path in self._entries():
            expires_at, mtime = self._header(path)
            if expires_at is MISSING or (expires_at is not None and expires_at <= now):
                removed += self._unlink(path)
            else:
                live.append((mtime, path))

        if self.max_entries is not None and len(live) > self.max_entries:
            live.sort()  # 按 mtime 从旧到新排序
            for _, path in live[: len(live) - self.max_entries]:
                removed += self._unlink(path)

        self._drop_empty_shards()
        return removed

    def close(self) -> None:
        """本实现不持有任何长驻句柄；仅为满足接口对称性而存在。"""

    # -- 内部实现 --------------------------------------------------------

    def _shards(self) -> Iterable[str]:
        try:
            names = os.listdir(self.directory)
        except OSError:
            return
        for name in names:
            # 只处理属于自己的分片目录：cache_dir 可能与无关文件共享，
            # clear() 不能变成 "rm -rf"。
            if len(name) == 2 and all(c in "0123456789abcdef" for c in name):
                path = os.path.join(self.directory, name)
                if os.path.isdir(path):
                    yield path

    def _entries(self) -> Iterable[str]:
        for shard in self._shards():
            try:
                names = os.listdir(shard)
            except OSError:
                continue
            for name in names:
                if name.endswith(_SUFFIX):
                    yield os.path.join(shard, name)

    @staticmethod
    def _header(path: str) -> tuple[Any, float]:
        """返回 ``(expires_at, mtime)``；不可读时 ``expires_at`` 为 MISSING。"""
        try:
            mtime = os.stat(path).st_mtime
            with open(path, "rb") as handle:
                return pickle.load(handle), mtime
        except OSError:
            return MISSING, 0.0
        except Exception:
            return MISSING, 0.0

    def _drop_empty_shards(self) -> None:
        for shard in self._shards():
            with contextlib.suppress(OSError):
                os.rmdir(shard)

    def _maybe_trim(self) -> None:
        if self.max_entries is None:
            return
        with self._lock:
            self._writes += 1
            due = self._writes >= _TRIM_INTERVAL
            if due:
                self._writes = 0
        if due:
            self.prune()


class PickleCache(FunctionCache):
    """将函数结果以 pickle 文件形式缓存到磁盘。

    Args:
        cache_key: 参数名、参数名序列，或 ``None`` 表示以全部参数为键。
        cache_dir: 缓存文件所在目录，相对路径在装饰时解析一次。
        is_cache: 用于逐次调用开关缓存的参数名。
        expire: 条目存活时间（秒）；``None`` 表示永不过期。
        max_entries: 已存条目数的软上限，由后台裁剪与
            :meth:`~farcache.CachedFunction.cache_prune` 共同保证；
            ``None`` 表示不限制。
        printf: 兼容旧版的开关，开启后额外通过 ``farlog`` 记录缓存事件
            （不再直接输出到 stdout）。
    """

    def __init__(
        self,
        cache_key: str | Iterable[str] | None = None,
        cache_dir: str = ".cache",
        is_cache: str = "cache",
        expire: float | None = None,
        max_entries: int | None = None,
        printf: bool = False,
    ) -> None:
        super().__init__(cache_key=cache_key, is_cache=is_cache)
        self.cache_dir = cache_dir
        self.expire = expire
        self.max_entries = max_entries
        self.printf = printf

    def _prepare(self, func: Callable[..., Any]) -> str:
        return os.path.abspath(self.cache_dir)

    def _create_store(self, prepared: str) -> CacheStore:
        return PickleStore(prepared, self.expire, self.max_entries)

    def _report(self, event: str, func: Callable[..., Any]) -> None:
        super()._report(event, func)
        if self.printf:
            logger.info("{} for function {!r}", event, namespace_of(func))


def pkl_cache(
    cache_key: str | Iterable[str] | None = None,
    cache_dir: str = ".cache",
    is_cache: str = "cache",
    expire: float | None = None,
    max_entries: int | None = None,
    printf: bool = False,
) -> PickleCache:
    """:class:`PickleCache` 的便捷工厂函数。

    Args:
        cache_key: 参数名、参数名序列，或 ``None`` 表示以全部参数为键。
        cache_dir: 缓存文件所在目录。
        is_cache: 用于逐次调用开关缓存的参数名。
        expire: 条目存活时间（秒）；``None`` 表示永不过期。
        max_entries: 已存条目数的软上限；``None`` 表示不限制。
        printf: 兼容旧版的开关，开启后额外通过 ``farlog`` 记录缓存事件。

    Returns:
        新建的 :class:`PickleCache` 实例。
    """
    return PickleCache(
        cache_key=cache_key,
        cache_dir=cache_dir,
        is_cache=is_cache,
        expire=expire,
        max_entries=max_entries,
        printf=printf,
    )
