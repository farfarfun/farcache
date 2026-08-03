import hashlib
import inspect
import os
import pickle
import tempfile
from functools import cached_property, wraps
from typing import Any, Callable

from ._utils import bind_args

_MISSING = object()

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

    def _get_cache_file(self, namespace: str, key: Any) -> str:
        payload = pickle.dumps((namespace, key), protocol=pickle.HIGHEST_PROTOCOL)
        hashed = hashlib.sha256(payload).hexdigest()
        return os.path.join(self.cache_dir, f"{hashed}.pkl")

    @staticmethod
    def _load_cache(cache_file: str) -> Any:
        try:
            with open(cache_file, "rb") as f:
                return pickle.load(f)
        except FileNotFoundError:
            return _MISSING
        except (EOFError, pickle.PickleError, AttributeError, ImportError, IndexError):
            try:
                os.unlink(cache_file)
            except OSError:
                pass
            return _MISSING

    def _save_cache(self, cache_file: str, data: Any) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.cache_dir, delete=False
            ) as f:
                temp_file = f.name
                pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(temp_file, cache_file)
        finally:
            if temp_file:
                try:
                    os.unlink(temp_file)
                except FileNotFoundError:
                    pass

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

            is_cache = bound.get(self.is_cache, True)
            cache_key = bound[self.cache_key]
            is_cache = is_cache and cache_key is not None

            if is_cache:
                cache_file = self._get_cache_file(namespace, cache_key)
                cached_result = self._load_cache(cache_file)
                if cached_result is not _MISSING:
                    self._log(f"Cache hit for function '{func.__name__}'")
                    return cached_result

            result = func(*args, **kwargs)

            if is_cache:
                self._save_cache(cache_file, result)
                self._log(f"Cache data for function '{func.__name__}'")
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
