"""Lightweight function caching decorators for memory and disk."""

from importlib.metadata import PackageNotFoundError, version

from ._base import CachedFunction, CacheStore, FunctionCache
from ._keys import UnstableKeyError
from .box import (
    cache,
    fifo_cache,
    lfu_cache,
    lru_cache,
    rr_cache,
    ttl_cache,
    vttl_cache,
)
from .core import PickleCache, PickleStore, cached_property, pkl_cache
from .disk import DiskCache, DiskStore, disk_cache

try:
    __version__ = version("nltcache")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0.dev0"

__all__ = [
    "__version__",
    # memory
    "cache",
    "lru_cache",
    "ttl_cache",
    "vttl_cache",
    "lfu_cache",
    "fifo_cache",
    "rr_cache",
    # disk
    "PickleCache",
    "PickleStore",
    "pkl_cache",
    "DiskCache",
    "DiskStore",
    "disk_cache",
    # shared
    "CacheStore",
    "CachedFunction",
    "FunctionCache",
    "UnstableKeyError",
    "cached_property",
]
