"""Internal utilities shared across nltcache modules."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

__all__ = ["bind_args", "namespace_of"]


def bind_args(
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Bind a call without changing the function's native calling semantics."""
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    return bound.arguments


def namespace_of(func: Callable[..., Any]) -> str:
    """Return a stable identity for *func*, used to isolate cache entries."""
    module = getattr(func, "__module__", "") or ""
    name = (
        getattr(func, "__qualname__", None)
        or getattr(func, "__name__", None)
        or type(func).__name__
    )
    return f"{module}.{name}"
