"""Shared machinery for the parameter-keyed persistent caches.

``PickleCache`` and ``DiskCache`` differ only in where bytes land, so signature
validation, key derivation, the ``is_cache`` escape hatch, sync/async wrapping
and the introspection API all live here. Backends implement :class:`CacheStore`.
"""

from __future__ import annotations

import functools
import inspect
import logging
import threading
from collections.abc import Callable, Iterable
from typing import (
    Any,
    ParamSpec,
    Protocol,
    TypeVar,
    cast,
)

from ._keys import UnstableKeyError, key_digest
from ._utils import bind_args, namespace_of

P = ParamSpec("P")
R = TypeVar("R")

__all__ = ["MISSING", "CacheStore", "CachedFunction", "FunctionCache"]

logger = logging.getLogger("nltcache")


class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISSING>"

    def __bool__(self) -> bool:
        return False


#: Sentinel for "not in the cache". ``None`` is a cacheable value, so it cannot
#: double as the miss marker.
MISSING: Any = _Missing()


class CacheStore(Protocol):
    """Backing store for a single decorated function."""

    def get(self, digest: str) -> Any:
        """Return the stored value, or :data:`MISSING`."""

    def set(self, digest: str, value: Any) -> None: ...

    def delete(self, digest: str) -> bool:
        """Remove one entry; return whether it existed."""

    def clear(self) -> int:
        """Remove every entry; return how many were removed."""

    def prune(self) -> int:
        """Remove expired entries; return how many were removed."""

    def close(self) -> None: ...


class CachedFunction(Protocol[P, R]):
    """A decorated function, plus the cache-control API attached to it."""

    __wrapped__: Callable[P, R]

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R: ...

    def cache_key(self, *args: P.args, **kwargs: P.kwargs) -> str | None:
        """Digest the given call would use, or ``None`` if it bypasses caching."""

    def cache_invalidate(self, *args: P.args, **kwargs: P.kwargs) -> bool:
        """Drop the entry for the given call; return whether one existed."""

    def cache_clear(self) -> int: ...

    def cache_prune(self) -> int: ...

    def cache_close(self) -> None: ...


class _FunctionState:
    """Per-decorated-function state: lazily built store, created once."""

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
    """Base class for caches keyed on a subset of a function's parameters.

    Args:
        cache_key: Parameter name, sequence of parameter names, or ``None`` to
            key on every argument.
        is_cache: Name of a parameter that toggles caching per call.
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

    # -- backend hooks ----------------------------------------------------

    def _prepare(self, func: Callable[..., Any]) -> Any:
        """Resolve per-function configuration at decoration time (no I/O)."""
        return None

    def _create_store(self, prepared: Any) -> CacheStore:
        """Open the backing store. Called once, on the first cached call."""
        raise NotImplementedError

    def _report(self, event: str, func: Callable[..., Any]) -> None:
        logger.debug("%s for %s", event, namespace_of(func))

    # -- public API -------------------------------------------------------

    def close(self) -> None:
        """Release every store this decorator opened."""
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
            """Digest for this call, or None when the call must bypass the cache."""
            bound = bind_args(signature, args, kwargs)

            if not bound.get(self.is_cache, True):
                return None

            if key_names is None:
                # Toggling caching must not change where a result is stored.
                material: Any = {
                    name: value
                    for name, value in bound.items()
                    if name != self.is_cache
                }
            else:
                material = [bound[name] for name in key_names]
                # A None key is the documented "nothing to key on" escape hatch.
                if any(value is None for value in material):
                    return None

            try:
                return key_digest(namespace, material)
            except UnstableKeyError:
                if key_names is not None:
                    # The parameter was nominated explicitly; failing silently
                    # would turn a typo into a permanent cache miss.
                    raise
                if not state.warned:
                    state.warned = True
                    logger.warning(
                        "%s: arguments are not reproducibly hashable, caching "
                        "disabled for this function",
                        namespace,
                        exc_info=True,
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
