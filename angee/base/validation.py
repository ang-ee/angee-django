"""Translate typed declaration validation into Django's field error contract."""

from functools import cache
from typing import Any

from django.core.exceptions import ValidationError
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError


@cache
def _adapter(type_: Any) -> TypeAdapter[Any]:
    """Reuse native parsing for each declared value type."""

    return TypeAdapter(Any if type_ is None else type_)


def validate_value(type_: Any, value: Any, *, field: str) -> Any:
    """Validate a typed value with error paths rooted at the owning field."""

    try:
        return _adapter(type_).validate_python(value)
    except PydanticValidationError as error:
        messages: dict[str, list[str]] = {}
        for issue in error.errors(include_url=False, include_context=False, include_input=False):
            location = ".".join(str(part) for part in issue["loc"])
            path = f"{field}.{location}" if location else field
            messages.setdefault(path, []).append(str(issue["msg"]))
        raise ValidationError(messages) from None
