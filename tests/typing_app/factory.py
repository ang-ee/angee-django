"""An unrelated factory shares Django's method name in a typed import cycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.typing_app.models import FrameworkRecord


class OtherFactory[T: FrameworkRecord]:
    @classmethod
    def from_queryset(cls, value: int) -> str:
        return str(value)
