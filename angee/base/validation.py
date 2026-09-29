"""Translate typed declaration validation into Django's field error contract."""

from typing import Any

from django.core.exceptions import ValidationError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError


def validate_model[T: BaseModel](model: type[T], value: Any, *, field: str) -> T:
    """Validate a Pydantic value with error paths rooted at the owning field."""

    try:
        return model.model_validate(value)
    except PydanticValidationError as error:
        messages: dict[str, list[str]] = {}
        for issue in error.errors(include_url=False, include_context=False, include_input=False):
            location = ".".join(str(part) for part in issue["loc"])
            path = f"{field}.{location}" if location else field
            messages.setdefault(path, []).append(str(issue["msg"]))
        raise ValidationError(messages) from None
