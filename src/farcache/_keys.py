"""确定性缓存键推导。

``pickle.dumps`` 不能直接作为缓存键的基础：`set`（或任何归约为 `set` 的对象）
产生的字节流依赖字符串哈希随机化，导致同一个逻辑键在每个进程中哈希结果都不同。
本模块改为遍历值本身并生成规范编码，其中无序容器会先排序，从而得到跨进程、
跨解释器重启都稳定的摘要。
"""

from __future__ import annotations

import hashlib
import types
from typing import Any

__all__ = ["PICKLE_PROTOCOL", "canonical_bytes", "key_digest"]

#: 有意固定：`pickle.HIGHEST_PROTOCOL` 会随解释器版本变化，
#: 若放任其漂移，升级 Python 就会悄悄让所有已存条目失效。
PICKLE_PROTOCOL = 5

_MAX_DEPTH = 64

# pickle 通过限定名而非归约来引用的对象类型。
_BY_NAME = (type, types.FunctionType, types.BuiltinFunctionType, types.ModuleType)


class UnstableKeyError(TypeError):
    """当无法从某个值推导出可复现的键时抛出。"""


def key_digest(*parts: Any) -> str:
    """返回一个十六进制摘要，对相等的 *parts* 在不同进程间保持稳定。

    Args:
        *parts: 参与摘要计算的任意数量的值。

    Returns:
        十六进制编码的 sha256 摘要字符串。
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(canonical_bytes(part))
        digest.update(b"\x1e")
    return digest.hexdigest()


def canonical_bytes(obj: Any) -> bytes:
    """将 *obj* 编码为字节串，相等的值编码结果也相等。

    Args:
        obj: 待编码的任意值。

    Returns:
        规范化编码后的字节串。
    """
    buffer = bytearray()
    _encode(obj, buffer, set(), 0)
    return bytes(buffer)


def _qualified_name(obj: Any) -> str:
    module = getattr(obj, "__module__", "") or ""
    name = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None)
    return f"{module}.{name}" if name else repr(obj)


def _identity_of(obj: Any) -> str:
    """为按引用编码的对象命名；匿名对象（如 lambda）用定义位置消歧。"""
    name = _qualified_name(obj)
    if "<lambda>" in name:
        code = getattr(obj, "__code__", None)
        if code is not None:
            return f"{name}@{code.co_filename}:{code.co_firstlineno}"
    return name


def _tagged(buffer: bytearray, tag: bytes, payload: bytes) -> None:
    # 长度前缀保证拼接后的 payload 不会歧义，因此 ("ab", "c") 不会与 ("a", "bc") 冲突。
    buffer += tag
    buffer += b"%d:" % len(payload)
    buffer += payload


def _sub(obj: Any, seen: set[int], depth: int) -> bytes:
    buffer = bytearray()
    _encode(obj, buffer, seen, depth)
    return bytes(buffer)


def _encode(obj: Any, buffer: bytearray, seen: set[int], depth: int) -> None:
    if depth > _MAX_DEPTH:
        raise UnstableKeyError(
            f"cache key nests deeper than {_MAX_DEPTH} levels; pass a simpler key"
        )

    kind = type(obj)

    if obj is None:
        buffer += b"N;"
    elif kind is bool:
        buffer += b"T;" if obj else b"F;"
    elif kind is int:
        _tagged(buffer, b"i", b"%d" % obj)
    elif kind is float:
        _tagged(buffer, b"d", repr(obj).encode("ascii"))
    elif kind is complex:
        _tagged(buffer, b"c", repr(obj).encode("ascii"))
    elif kind is str:
        _tagged(buffer, b"s", obj.encode("utf-8", "surrogatepass"))
    elif kind is bytes:
        _tagged(buffer, b"y", obj)
    elif kind is bytearray:
        _tagged(buffer, b"Y", bytes(obj))
    elif kind is list or kind is tuple:
        _encode_sequence(obj, b"l" if kind is list else b"t", buffer, seen, depth)
    elif kind is set or kind is frozenset:
        _encode_unordered(obj, b"e" if kind is set else b"E", buffer, seen, depth)
    elif kind is dict:
        _encode_mapping(obj, buffer, seen, depth)
    elif isinstance(obj, _BY_NAME):
        _tagged(buffer, b"q", _identity_of(obj).encode("utf-8"))
    else:
        _encode_reduced(obj, buffer, seen, depth)


def _encode_sequence(
    obj: Any, tag: bytes, buffer: bytearray, seen: set[int], depth: int
) -> None:
    marker = id(obj)
    if marker in seen:
        buffer += b"R;"
        return
    seen.add(marker)
    try:
        buffer += tag + b"%d[" % len(obj)
        for item in obj:
            _encode(item, buffer, seen, depth + 1)
        buffer += b"]"
    finally:
        seen.discard(marker)


def _encode_unordered(
    obj: Any, tag: bytes, buffer: bytearray, seen: set[int], depth: int
) -> None:
    marker = id(obj)
    if marker in seen:
        buffer += b"R;"
        return
    seen.add(marker)
    try:
        # 对编码结果排序才能让 set 类型的键可复现；set 本身的迭代顺序
        # 依赖 PYTHONHASHSEED。
        parts = sorted(_sub(item, seen, depth + 1) for item in obj)
    finally:
        seen.discard(marker)
    buffer += tag + b"%d[" % len(parts)
    for part in parts:
        buffer += part
    buffer += b"]"


def _encode_mapping(obj: Any, buffer: bytearray, seen: set[int], depth: int) -> None:
    marker = id(obj)
    if marker in seen:
        buffer += b"R;"
        return
    seen.add(marker)
    try:
        # 相等的映射无论插入顺序如何都应相等，键的编码也应如此。
        parts = sorted(
            (_sub(key, seen, depth + 1), _sub(value, seen, depth + 1))
            for key, value in obj.items()
        )
    finally:
        seen.discard(marker)
    buffer += b"m%d[" % len(parts)
    for key_bytes, value_bytes in parts:
        buffer += key_bytes
        buffer += value_bytes
    buffer += b"]"


def _encode_reduced(obj: Any, buffer: bytearray, seen: set[int], depth: int) -> None:
    """通过 pickle 归约结果编码任意对象。

    递归处理归约结果而非直接 pickle 它，可以让嵌套的 set/mapping
    获得与顶层对象一致的规范化处理。
    """
    kind = type(obj)
    reduce_ex = getattr(obj, "__reduce_ex__", None)
    if reduce_ex is None:
        raise UnstableKeyError(
            f"cannot derive a stable cache key from {_qualified_name(kind)!r}"
        )
    try:
        reduced = reduce_ex(PICKLE_PROTOCOL)
    except Exception as exc:  # 不可 pickle：锁、文件句柄、lambda 等
        raise UnstableKeyError(
            f"cannot derive a stable cache key from {_qualified_name(kind)!r}: {exc}"
        ) from exc

    marker = id(obj)
    if marker in seen:
        buffer += b"R;"
        return
    seen.add(marker)
    try:
        _tagged(buffer, b"T", _qualified_name(kind).encode("utf-8"))
        if isinstance(reduced, str):
            # 按名称 pickle 的情况，例如模块级单例。
            _tagged(buffer, b"q", reduced.encode("utf-8"))
        else:
            _encode(tuple(reduced), buffer, seen, depth + 1)
    finally:
        seen.discard(marker)
