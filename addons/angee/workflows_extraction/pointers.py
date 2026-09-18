"""Pure JSON Pointer transforms used by retained extraction layers."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from angee.workflows.attempts import json_values_equal

JSON_POINTER_MISSING = object()


def _json_pointer_tokens(pointer: str) -> tuple[str, ...] | None:
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        return None
    tokens = []
    for encoded in pointer[1:].split("/"):
        if "~" in encoded:
            index = 0
            while (index := encoded.find("~", index)) >= 0:
                if index + 1 >= len(encoded) or encoded[index + 1] not in {"0", "1"}:
                    return None
                index += 2
        tokens.append(encoded.replace("~1", "/").replace("~0", "~"))
    return tuple(tokens)


def _json_pointer_tokens_value(
    value: Any,
    tokens: tuple[str, ...],
    *,
    array_element_baseline: Any = JSON_POINTER_MISSING,
) -> Any:
    current = value
    baseline = array_element_baseline
    for token in tokens:
        if isinstance(current, Mapping):
            if token not in current:
                return JSON_POINTER_MISSING
            current = current[token]
            if baseline is not JSON_POINTER_MISSING:
                if not isinstance(baseline, Mapping) or token not in baseline:
                    return JSON_POINTER_MISSING
                baseline = baseline[token]
        elif isinstance(current, list):
            if not token.isascii() or not token.isdigit() or (token.startswith("0") and token != "0"):
                return JSON_POINTER_MISSING
            index = int(token)
            if index >= len(current):
                return JSON_POINTER_MISSING
            current = current[index]
            if baseline is not JSON_POINTER_MISSING:
                if not isinstance(baseline, list) or index >= len(baseline):
                    return JSON_POINTER_MISSING
                baseline = baseline[index]
                if not json_values_equal(current, baseline):
                    return JSON_POINTER_MISSING
        else:
            return JSON_POINTER_MISSING
    return current


def json_pointer_value(value: Any, pointer: str) -> Any:
    """Resolve one RFC 6901 pointer or raise ``KeyError`` when absent."""

    if pointer == "":
        return value
    resolved = json_pointer_value_or_missing(value, pointer)
    if resolved is JSON_POINTER_MISSING:
        raise KeyError(pointer)
    return resolved


def json_pointer_value_or_missing(
    value: Any,
    pointer: str,
    *,
    array_element_baseline: Any = JSON_POINTER_MISSING,
) -> Any:
    """Resolve a pointer while retaining the internal missing-value contract."""

    tokens = _json_pointer_tokens(pointer)
    if tokens is None:
        return JSON_POINTER_MISSING
    return _json_pointer_tokens_value(
        value,
        tokens,
        array_element_baseline=array_element_baseline,
    )


def set_json_pointer(value: Any, pointer: str, replacement: Any) -> None:
    """Set one already-validated non-root RFC 6901 pointer value in place."""

    fields = _json_pointer_tokens(pointer)
    if not fields:
        raise KeyError(pointer)
    parent = value
    for field in fields[:-1]:
        parent = parent[int(field)] if isinstance(parent, list) else parent[field]
    last = fields[-1]
    if isinstance(parent, list):
        parent[int(last)] = replacement
    else:
        parent[last] = replacement


def materialize_missing_json_pointer_path(
    value: Any,
    pointer: str,
    *,
    source: Any,
    source_pointer: str,
) -> None:
    """Copy the nearest missing source subtree needed by one valid pointer."""

    destination = _json_pointer_tokens(pointer)
    retained = _json_pointer_tokens(source_pointer)
    if not destination or retained is None or len(destination) != len(retained):
        raise KeyError(pointer)
    for depth in range(len(destination) - 1, -1, -1):
        parent = _json_pointer_tokens_value(value, destination[:depth])
        if parent is JSON_POINTER_MISSING:
            continue
        retained_child = _json_pointer_tokens_value(source, retained[: depth + 1])
        if retained_child is JSON_POINTER_MISSING:
            break
        token = destination[depth]
        if isinstance(parent, dict):
            if token in parent:
                break
            parent[token] = deepcopy(retained_child)
        elif isinstance(parent, list):
            if (
                not token.isascii()
                or not token.isdigit()
                or (token.startswith("0") and token != "0")
                or int(token) != len(parent)
            ):
                break
            parent.append(deepcopy(retained_child))
        else:
            break
        if _json_pointer_tokens_value(value, destination) is not JSON_POINTER_MISSING:
            return
        break
    raise KeyError(pointer)
