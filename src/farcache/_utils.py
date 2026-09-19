"""farcache 内部模块间共享的工具函数。"""

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
    """按签名绑定一次调用的实参，不改变函数原本的调用语义。

    Args:
        signature: 目标函数的签名。
        args: 位置实参。
        kwargs: 关键字实参。

    Returns:
        参数名到实参值的映射（已补齐默认值）。
    """
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    return bound.arguments


def namespace_of(func: Callable[..., Any]) -> str:
    """返回 *func* 的稳定标识，用于隔离不同函数的缓存条目。

    Args:
        func: 目标可调用对象。

    Returns:
        形如 ``模块名.限定名`` 的字符串。
    """
    module = getattr(func, "__module__", "") or ""
    name = (
        getattr(func, "__qualname__", None)
        or getattr(func, "__name__", None)
        or type(func).__name__
    )
    return f"{module}.{name}"
