"""Internal utilities shared across nltcache modules."""

from __future__ import annotations

import inspect
from typing import Any


def bind_args(
    signature: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Bind a call without changing the function's native calling semantics."""
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    return bound.arguments
