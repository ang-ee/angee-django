"""Pure grounding of scalar inference candidates in retained text evidence."""

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from jsonpointer import JsonPointer

from angee.workflows_extraction.contracts import DocumentPart

RETAINED_AUTHORITY_COMPLETION_REVIEW = "retained_authority_completion_requires_review"
"""A retained authority completion still requires an explicit review."""
RETAINED_CARRIER_UNAVAILABLE = "retained_carrier_unavailable"
"""The carrier required to ground a retained fact is unavailable."""

_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_NUMBER_TOKEN = re.compile(r"(?<![\w./-])[-+]?\d+(?:[.,]\d+)?(?![\w./-])")


def derive_text_claims(value: Any, parts: Sequence[DocumentPart]) -> dict[str, list[dict[str, Any]]]:
    """Derive exact scalar spans from text; inference output never supplies provenance."""
    claims: dict[str, list[dict[str, Any]]] = {}

    def visit(item: Any, path: tuple[str | int, ...]) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, (*path, str(key)))
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, (*path, index))
        elif item not in (None, "") and not isinstance(item, bool):
            matches = []
            for position, part in enumerate(parts):
                if not isinstance(part.value, str):
                    continue
                span = _grounded_span(str(item), part.value, numeric_scalar=isinstance(item, (int, float, Decimal)))
                if span is not None:
                    matches.append({"part_position": position, "start": span[0], "end": span[1]})
            if matches:
                claims[JsonPointer.from_parts(path).path] = matches

    visit(value, ())
    return claims


def _grounded_span(needle: str, evidence: str, *, numeric_scalar: bool) -> tuple[int, int] | None:
    if not _NUMBER.fullmatch(needle):
        start = evidence.find(needle)
        return (start, start + len(needle)) if start >= 0 else None
    for match in _NUMBER_TOKEN.finditer(evidence):
        candidate = match.group()
        if candidate == needle or _decimal_equivalent(needle, candidate, numeric_scalar=numeric_scalar):
            return match.span()
    return None


def _decimal_equivalent(left: str, right: str, *, numeric_scalar: bool) -> bool:
    if not ({".", ","} & set(right)) or (not numeric_scalar and not ({".", ","} & set(left))):
        return False
    if numeric_scalar and not ({".", ","} & set(left)) and not re.fullmatch(r"[-+]?\d+[.,]0{1,2}", right):
        return False
    try:
        return Decimal(left.replace(",", ".")) == Decimal(right.replace(",", "."))
    except InvalidOperation:
        return False
