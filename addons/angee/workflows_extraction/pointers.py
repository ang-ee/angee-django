"""Pure JSON Pointer transforms used by retained extraction layers."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from django.core.exceptions import ValidationError

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


def result_selectors(
    result: Any,
    layout: Any,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Expand domain-declared JSON locators without reading domain field names."""

    if not isinstance(result, dict) or not result:
        return ()
    if not isinstance(layout, dict):
        raise ValidationError({"extraction": "The evidence layout is invalid."})
    document_collection = layout.get("document_collection", "")
    line_collection = layout.get("line_collection", "")
    root_document_on_missing = layout.get("root_document_on_missing", False)
    if type(root_document_on_missing) is not bool:
        raise ValidationError({"extraction": "The root document fallback policy must be a boolean."})
    if not all(
        isinstance(pointer, str) and (not pointer or pointer.startswith("/"))
        for pointer in (document_collection, line_collection)
    ):
        raise ValidationError({"extraction": "The evidence layout requires JSON pointers."})
    try:
        documents = json_pointer_value(result, document_collection) if document_collection else None
    except KeyError:
        if not root_document_on_missing:
            raise ValidationError({"extraction": "The declared document collection is absent."})
        documents = None
    if documents is not None and not isinstance(documents, list):
        raise ValidationError({"extraction": "The declared document collection must be a list."})
    if isinstance(documents, list):
        items = tuple((f"{document_collection}/{index}", document) for index, document in enumerate(documents))
    else:
        items = (("", result),)
    selectors = []
    for selector, document in items:
        if not isinstance(document, dict):
            raise ValidationError({"extraction": "A logical document must be an object."})
        try:
            lines = json_pointer_value(document, line_collection) if line_collection else None
        except KeyError:
            lines = None
        if lines is not None and not isinstance(lines, list):
            raise ValidationError({"extraction": "The declared source-line collection must be a list."})
        selectors.append(
            (
                selector,
                tuple(f"{selector}{line_collection}/{index}" for index in range(len(lines or ()))),
            )
        )
    return tuple(selectors)


def implicit_identity_correspondence(
    result: Any,
    *,
    layout: Any,
    original: Any,
) -> dict[str, str] | None:
    """Return the complete correspondence only for the proven safe carry cases."""

    requested = result_selectors(result, layout)
    previous = tuple(original.document_refs)
    if not previous:
        return {}
    if json_values_equal(original.result, result):
        return {
            selector: identity
            for ref in previous
            for selector, identity in (
                (ref.selector, ref.identity),
                *((line.selector, line.identity) for line in ref.lines),
            )
        }
    if len(previous) != 1 or len(requested) != 1:
        return None
    document = previous[0]
    selector, line_selectors = requested[0]
    if document.selector != selector:
        return None
    mapping = {selector: document.identity}
    if not document.lines:
        mapping.update({line_selector: "new" for line_selector in line_selectors})
        return mapping
    retained_line_selectors = tuple(line.selector for line in document.lines)
    if retained_line_selectors != line_selectors:
        return None
    if len(line_selectors) == 1:
        mapping[line_selectors[0]] = document.lines[0].identity
        return mapping
    if line_selectors:
        try:
            unchanged = all(
                json_values_equal(
                    json_pointer_value(original.result, line_selector),
                    json_pointer_value(result, line_selector),
                )
                for line_selector in line_selectors
            )
        except KeyError:
            return None
        if unchanged:
            mapping.update({line.selector: line.identity for line in document.lines})
            return mapping
    return None


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
