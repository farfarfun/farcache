"""Pickle-file backed function cache."""

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

from ._base import MISSING, CacheStore, FunctionCache
from ._keys import PICKLE_PROTOCOL
from ._utils import namespace_of

__all__ = ["PickleCache", "PickleStore", "cached_property", "pkl_cache"]

_SUFFIX = ".pkl"

#: Trimming to ``max_entries`` needs a directory scan, so amortise it over
#: several writes instead of paying for it on every store.
_TRIM_INTERVAL = 256


class PickleStore(CacheStore):
    """One pickle file per entry, written atomically.

    Files are sharded into 256 subdirectories by digest prefix, so a large cache
    does not degrade into a single huge directory. Each file holds two pickles:
    the expiry stamp first, then the value, which lets expiry be checked without
    deserialising the payload.
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
            # Truncated, half-written by an older version, or referencing a
            # class that has since been renamed: drop it and recompute.
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
            temp_path = None  # consumed by the rename
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
        """Drop expired entries, then trim to ``max_entries`` oldest-first."""
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
            live.sort()  # oldest mtime first
            for _, path in live[: len(live) - self.max_entries]:
                removed += self._unlink(path)

        self._drop_empty_shards()
        return removed

    def close(self) -> None:
        """No persistent handles are held; present for interface symmetry."""

    # -- internals --------------------------------------------------------

    def _shards(self) -> Iterable[str]:
        try:
            names = os.listdir(self.directory)
        except OSError:
            return
        for name in names:
            # Only ever touch our own shard directories: cache_dir may be shared
            # with unrelated files, and clear() must not become "rm -rf".
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
        """Return ``(expires_at, mtime)``; ``expires_at`` is MISSING if unreadable."""
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
    """Cache function results as pickle files on disk.

    Args:
        cache_key: Parameter name, sequence of parameter names, or ``None`` to
            key on every argument.
        cache_dir: Directory for the cache files. Relative paths are resolved
            once, at decoration time.
        is_cache: Name of a parameter that toggles caching per call.
        expire: Entry lifetime in seconds; ``None`` means never expire.
        max_entries: Soft cap on stored entries, enforced periodically and by
            :meth:`~farcache.CachedFunction.cache_prune`. ``None`` is unbounded.
        printf: Legacy flag; also echoes cache events to stdout. Prefer
            configuring the ``farcache`` logger.
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
            print(f"{event} for function {namespace_of(func)!r}")


def pkl_cache(
    cache_key: str | Iterable[str] | None = None,
    cache_dir: str = ".cache",
    is_cache: str = "cache",
    expire: float | None = None,
    max_entries: int | None = None,
    printf: bool = False,
) -> PickleCache:
    """Convenience factory for :class:`PickleCache`."""
    return PickleCache(
        cache_key=cache_key,
        cache_dir=cache_dir,
        is_cache=is_cache,
        expire=expire,
        max_entries=max_entries,
        printf=printf,
    )
