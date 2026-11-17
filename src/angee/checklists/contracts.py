"""The checklist a model declares and the outcome vocabulary its results use."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from django.db import models


class ChecklistStatus(models.TextChoices):
    """The closed outcome vocabulary of one checklist item."""

    PASSED = "passed", "Passed"
    RISK = "risk", "Risk"
    CONFLICT = "conflict", "Conflict"
    UNRESOLVED = "unresolved", "Unresolved"
    NOT_CHECKED = "not_checked", "Not checked"
    NOT_APPLICABLE = "not_applicable", "Not applicable"


ATTENTION_STATUSES: tuple[ChecklistStatus, ...] = (
    ChecklistStatus.CONFLICT,
    ChecklistStatus.RISK,
    ChecklistStatus.UNRESOLVED,
)
"""The statuses needing attention, strongest first; a record and its categories take the strongest."""

PASSING_STATUSES: tuple[ChecklistStatus, ...] = (ChecklistStatus.PASSED, ChecklistStatus.NOT_APPLICABLE)
"""The statuses a content change turns unresolved, and the ones a blocking item needs."""

RESERVED_CATEGORY_KEYS = frozenset({"status", "next_blocking"})
"""Category keys the record's own ``checklist_status`` and ``checklist_next_blocking`` filters claim."""


@dataclass(frozen=True)
class ChecklistCategory:
    """One labelled group of items; a checklist's categories are ordered as declared."""

    key: str
    label: str


@dataclass(frozen=True)
class ChecklistItem:
    """One declared item: its key, its label and the category it belongs to.

    ``blocking`` marks the items a consumer's gate reads (a decision's
    ``validate_verdict``, a verb's blocker); the checklist itself never refuses.
    """

    key: str
    label: str
    category: str | None = None
    blocking: bool = False


@dataclass(frozen=True)
class Checklist:
    """A model's ordered categories and items, extended by composed donors through ``super()``."""

    categories: tuple[ChecklistCategory, ...] = ()
    items: tuple[ChecklistItem, ...] = ()
    _index: dict[str, ChecklistItem] = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        """Index items by key; a duplicate is a declaration error the system check reports."""

        object.__setattr__(self, "_index", {item.key: item for item in self.items})

    def extend(
        self,
        *,
        categories: Iterable[ChecklistCategory] = (),
        items: Iterable[ChecklistItem] = (),
    ) -> Checklist:
        """Return this checklist with ``categories`` and ``items`` appended in order."""

        return Checklist(categories=(*self.categories, *categories), items=(*self.items, *items))

    @property
    def keys(self) -> tuple[str, ...]:
        """The declared item keys, in declaration order."""

        return tuple(item.key for item in self.items)

    def item(self, key: str) -> ChecklistItem | None:
        """Return the declared item for ``key``, or ``None`` for an undeclared or retired key."""

        return self._index.get(key)

    def category_items(self, category: str) -> tuple[ChecklistItem, ...]:
        """Return the items of one category, in declaration order."""

        return tuple(item for item in self.items if item.category == category)

    def problems(self) -> list[str]:
        """Describe duplicate keys and unknown or reserved categories, for the system check."""

        problems: list[str] = []
        category_keys = [category.key for category in self.categories]
        for key in sorted({key for key in category_keys if category_keys.count(key) > 1}):
            problems.append(f"category {key!r} is declared more than once")
        for key in sorted(set(category_keys) & RESERVED_CATEGORY_KEYS):
            problems.append(f"category {key!r} is reserved")
        item_keys = [item.key for item in self.items]
        for key in sorted({key for key in item_keys if item_keys.count(key) > 1}):
            problems.append(f"item {key!r} is declared more than once")
        for item in self.items:
            if item.category is not None and item.category not in category_keys:
                problems.append(f"item {item.key!r} names the undeclared category {item.category!r}")
        return problems
