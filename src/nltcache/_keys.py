"""Deterministic cache-key derivation.

``pickle.dumps`` is not a safe basis for cache keys: the byte stream for a
``set`` (or any object reducing to one) depends on string hash randomisation,
so the same logical key hashes differently in every process. This module walks
the value instead and emits a canonical encoding in which unordered containers
are sorted, giving digests that are stable across processes and interpreter
restarts.
"""

from __future__ import annotations

import hashlib
import types
from typing import Any

__all__ = ["PICKLE_PROTOCOL", "canonical_bytes", "key_digest"]

#: Pinned on purpose. ``pickle.HIGHEST_PROTOCOL`` drifts between interpreter
#: versions, and letting it drift would silently invalidate every stored entry
#: on upgrade.
PICKLE_PROTOCOL = 5

_MAX_DEPTH = 64

# Objects pickle refers to by qualified name rather than by reducing them.
_BY_NAME = (type, types.FunctionType, types.BuiltinFunctionType, types.ModuleType)


class UnstableKeyError(TypeError):
    """Raised when no reproducible key can be derived from a value."""


def key_digest(*parts: Any) -> str:
    """Return a hex digest that is stable across processes for equal *parts*."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(canonical_bytes(part))
        digest.update(b"\x1e")
    return digest.hexdigest()


def canonical_bytes(obj: Any) -> bytes:
    """Encode *obj* into a byte string that is equal for equal values."""
    buffer = bytearray()
    _encode(obj, buffer, set(), 0)
    return bytes(buffer)


def _qualified_name(obj: Any) -> str:
    module = getattr(obj, "__module__", "") or ""
    name = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None)
    return f"{module}.{name}" if name else repr(obj)


def _identity_of(obj: Any) -> str:
    """Name a by-reference object, disambiguating anonymous ones by defn site."""
    name = _qualified_name(obj)
    if "<lambda>" in name:
        code = getattr(obj, "__code__", None)
        if code is not None:
            return f"{name}@{code.co_filename}:{code.co_firstlineno}"
    return name


def _tagged(buffer: bytearray, tag: bytes, payload: bytes) -> None:
    # Length prefixes keep concatenated payloads unambiguous, so ("ab", "c")
    # cannot collide with ("a", "bc").
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
        # Sorting the *encodings* is what makes set keys reproducible; iteration
        # order of a set depends on PYTHONHASHSEED.
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
        # Equal mappings compare equal regardless of insertion order, so the key
        # should follow suit.
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
    """Encode an arbitrary object through its pickle reduction.

    Recursing into the reduction rather than pickling it directly means nested
    sets and mappings get the same canonical treatment as top-level ones.
    """
    kind = type(obj)
    reduce_ex = getattr(obj, "__reduce_ex__", None)
    if reduce_ex is None:
        raise UnstableKeyError(
            f"cannot derive a stable cache key from {_qualified_name(kind)!r}"
        )
    try:
        reduced = reduce_ex(PICKLE_PROTOCOL)
    except Exception as exc:  # unpicklable: locks, file handles, lambdas, ...
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
            # Pickled by name, e.g. a module-level singleton.
            _tagged(buffer, b"q", reduced.encode("utf-8"))
        else:
            _encode(tuple(reduced), buffer, seen, depth + 1)
    finally:
        seen.discard(marker)
