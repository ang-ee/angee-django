"""Work merge contributor invocation through the shared hook resolver."""

from __future__ import annotations

from django.test import override_settings

from angee.work.merge import run_task_merge_contributors

CALLS: list[tuple[str, object, object]] = []


def _first(source: object, canonical: object) -> None:
    CALLS.append(("first", source, canonical))


def _last(source: object, canonical: object) -> None:
    CALLS.append(("last", source, canonical))


def test_work_merge_sorts_and_deduplicates_declared_hooks() -> None:
    """Work keeps deterministic exactly-once mover order across addon contributions."""

    CALLS.clear()
    source, canonical = object(), object()
    with override_settings(ANGEE_WORK_MERGE_CONTRIBUTORS=[
        f"{__name__}._last", f"{__name__}._first", f"{__name__}._last",
    ]):
        run_task_merge_contributors(source, canonical)
    assert CALLS == [("first", source, canonical), ("last", source, canonical)]
