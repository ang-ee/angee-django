"""Strict JSON Pointer access and profile-declared evidence layout transforms."""

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from django.core.exceptions import ValidationError
from jsonpointer import JsonPointer, JsonPointerException

from angee.base.serialization import canonical_json

JSON_POINTER_MISSING = object()
"""Sentinel distinguishing an absent path from a retained JSON null."""


def _resolve(value: Any, pointer: JsonPointer, baseline: Any = JSON_POINTER_MISSING) -> Any:
    """Constrain upstream traversal to JSON containers and stable array elements."""
    for token in pointer.parts:
        if not isinstance(value, (Mapping, list)) or (isinstance(value, list) and token == "-"):
            raise JsonPointerException("The path does not reference a JSON value.")
        array = isinstance(value, list)
        if baseline is not JSON_POINTER_MISSING and not isinstance(baseline, list if array else Mapping):
            raise JsonPointerException("The baseline container differs from the retained value.")
        value = pointer.walk(value, token)
        if baseline is not JSON_POINTER_MISSING:
            baseline = _resolve(baseline, JsonPointer.from_parts([token]))
            if array and canonical_json(value) != canonical_json(baseline):
                raise JsonPointerException("The array element differs from its baseline.")
    return value


def json_pointer_value(value: Any, pointer: str) -> Any:
    """Resolve one RFC 6901 pointer or raise ``KeyError`` when invalid or absent."""
    try:
        return _resolve(value, JsonPointer(pointer))
    except JsonPointerException as error:
        raise KeyError(pointer) from error


def json_pointer_value_or_missing(
    value: Any,
    pointer: str,
    *,
    array_element_baseline: Any = JSON_POINTER_MISSING,
) -> Any:
    """Resolve a pointer, optionally proving each traversed array element unchanged."""
    try:
        return _resolve(value, JsonPointer(pointer), array_element_baseline)
    except JsonPointerException:
        return JSON_POINTER_MISSING


def set_json_pointer(value: Any, pointer: str, replacement: Any) -> None:
    """Set a non-root pointer in place; never index strings or append to arrays."""
    try:
        parsed = JsonPointer(pointer)
        if not parsed.parts:
            raise JsonPointerException("Cannot set the root in place.")
        parent = _resolve(value, JsonPointer.from_parts(parsed.parts[:-1]))
        if not isinstance(parent, (dict, list)) or (isinstance(parent, list) and parsed.parts[-1] == "-"):
            raise JsonPointerException("The parent is not a writable JSON container.")
        JsonPointer.from_parts(parsed.parts[-1:]).set(parent, replacement)
    except (JsonPointerException, IndexError) as error:
        raise KeyError(pointer) from error


def materialize_missing_json_pointer_path(
    value: Any,
    pointer: str,
    *,
    source: Any,
    source_pointer: str,
) -> None:
    """Copy the nearest missing source subtree needed by one valid pointer."""
    try:
        destination, retained = JsonPointer(pointer), JsonPointer(source_pointer)
        if not destination.parts or len(destination.parts) != len(retained.parts):
            raise JsonPointerException("Source and destination depths must agree.")
        for depth in range(len(destination.parts) - 1, -1, -1):
            try:
                parent = _resolve(value, JsonPointer.from_parts(destination.parts[:depth]))
            except JsonPointerException:
                continue
            child = _resolve(source, JsonPointer.from_parts(retained.parts[: depth + 1]))
            token = destination.parts[depth]
            if isinstance(parent, dict) and token not in parent:
                JsonPointer.from_parts([token]).set(parent, deepcopy(child))
            elif isinstance(parent, list) and JsonPointer.get_part(parent, token) == len(parent):
                parent.append(deepcopy(child))
            else:
                break
            _resolve(value, destination)
            return
    except JsonPointerException as error:
        raise KeyError(pointer) from error
    raise KeyError(pointer)


def result_selectors(result: Any, layout: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Expand profile-declared document and line pointers without interpreting keys."""
    if not isinstance(result, dict) or not result:
        return ()
    if not isinstance(layout, dict):
        raise ValidationError({"extraction": "The evidence layout is invalid."})
    document_collection = layout.get("document_collection", "")
    line_collection = layout.get("line_collection", "")
    root_document_on_missing = layout.get("root_document_on_missing", False)
    if type(root_document_on_missing) is not bool:
        raise ValidationError({"extraction": "The root document fallback policy must be a boolean."})
    try:
        for pointer in (document_collection, line_collection):
            if not isinstance(pointer, str):
                raise JsonPointerException("A pointer must be a string.")
            JsonPointer(pointer)
    except JsonPointerException as error:
        raise ValidationError({"extraction": "The evidence layout requires JSON pointers."}) from error
    try:
        documents = json_pointer_value(result, document_collection) if document_collection else None
    except KeyError:
        if not root_document_on_missing:
            raise ValidationError({"extraction": "The declared document collection is absent."}) from None
        documents = None
    if documents is not None and not isinstance(documents, list):
        raise ValidationError({"extraction": "The declared document collection must be a list."})
    items = (
        tuple((f"{document_collection}/{index}", document) for index, document in enumerate(documents))
        if isinstance(documents, list)
        else (("", result),)
    )
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
        selectors.append((selector, tuple(f"{selector}{line_collection}/{index}" for index in range(len(lines or ())))))
    return tuple(selectors)


def implicit_identity_correspondence(result: Any, *, layout: Any, original: Any) -> dict[str, str] | None:
    """Return a complete correspondence only when retained positions are unambiguous."""
    requested = result_selectors(result, layout)
    previous = tuple(original.document_refs)
    if not previous:
        return {}
    if canonical_json(original.result) == canonical_json(result):
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
        return mapping | dict.fromkeys(line_selectors, "new")
    if tuple(line.selector for line in document.lines) != line_selectors:
        return None
    if len(line_selectors) == 1:
        return mapping | {line_selectors[0]: document.lines[0].identity}
    try:
        unchanged = all(
            canonical_json(json_pointer_value(original.result, path))
            == canonical_json(json_pointer_value(result, path))
            for path in line_selectors
        )
    except KeyError:
        return None
    return mapping | {line.selector: line.identity for line in document.lines} if unchanged else None
