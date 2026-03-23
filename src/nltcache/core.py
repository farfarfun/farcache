import hashlib
import os
import pickle
from functools import cached_property, wraps
from typing import Any, Callable, Optional

from nltlog import getLogger

from ._utils import ensure_gitignore, normalize_args

logger = getLogger("funcache")

__all__ = ["PickleCache", "pkl_cache", "cached_property"]


class PickleCache:
    """Decorator that caches function results to pickle files on disk.

    Args:
        cache_key: The name of the function parameter whose value is used as the cache key.
        cache_dir: Directory to store pickle cache files.
        is_cache: The name of a boolean parameter that controls whether caching is enabled.
        printf: If True, also print cache log messages to stdout.
    """

    def __init__(
        self,
        cache_key: str,
        cache_dir: str = ".cache",
        is_cache: str = "cache",
        printf: bool = False,
    ) -> None:
        self.cache_key = cache_key
        self.cache_dir = cache_dir
        self.is_cache = is_cache
        self.printf = printf

    def _log(self, msg: str) -> None:
        if self.printf:
            print(msg)
        logger.debug(msg)

    def _get_cache_file(self, key: str) -> str:
        hashed = hashlib.md5(str(key).encode()).hexdigest()
        return os.path.join(self.cache_dir, f"{hashed}.pkl")

    @staticmethod
    def _load_cache(cache_file: str) -> Optional[Any]:
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except (FileNotFoundError, pickle.PickleError):
            return None

    def _save_cache(self, cache_file: str, data: Any) -> None:
        ensure_gitignore(self.cache_dir)
        with open(cache_file, "wb") as f:
            pickle.dump(data, f)

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            merged = normalize_args(func, args, kwargs)

            is_cache = merged.get(self.is_cache, True)
            cache_key = merged.get(self.cache_key, "")
            is_cache = is_cache and cache_key is not None

            if is_cache:
                cache_file = self._get_cache_file(cache_key)
                cached_result = self._load_cache(cache_file)
                if cached_result is not None:
                    self._log(
                        f"Cache hit for function '{func.__name__}' with key: {cache_key}"
                    )
                    return cached_result

            result = func(**merged)

            if is_cache:
                self._save_cache(cache_file, result)
                self._log(
                    f"Cache data for function '{func.__name__}' with key: {cache_key}"
                )
            return result

        return wrapper


def pkl_cache(
    cache_key: str,
    cache_dir: str = ".cache",
    is_cache: str = "cache",
    printf: bool = False,
) -> PickleCache:
    """Convenience factory for :class:`PickleCache`."""
    return PickleCache(cache_key, cache_dir, is_cache, printf=printf)
