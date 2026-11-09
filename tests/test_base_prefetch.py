"""Batch per reader, answer per row: the base prefetch adapter and reader memo."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rebac import SubjectRef, actor_context, system_context

from angee.base.prefetch import InstancePrefetch, ReaderMemo, prefetched, prefetched_objects

ALICE = SubjectRef.of("auth/user", "1")
BOB = SubjectRef.of("auth/user", "2")


def test_a_reader_memo_answers_only_the_reader_it_remembered() -> None:
    memo, row = ReaderMemo("_test_answers"), SimpleNamespace()
    memo.remember(row, "write", ALICE, False)
    assert memo.get(row, "write", ALICE) is False
    assert memo.get(row, "write", BOB) is None
    assert memo.get(row, "write", None) is None
    memo.forget(row)
    assert memo.get(row, "write", ALICE) is None


@pytest.mark.django_db
def test_a_reader_memo_never_remembers_without_an_actor_under_sudo_or_none() -> None:
    # Entering sudo writes REBAC's mandatory bypass audit row.
    memo, row = ReaderMemo("_test_answers"), SimpleNamespace()
    memo.remember(row, "write", None, True)
    memo.remember(row, "read", ALICE, None)
    with system_context(reason="test.reader_memo"):
        memo.remember(row, "graph", ALICE, True)
    assert vars(row) == {}


@pytest.mark.django_db
def test_a_reader_memo_answers_nothing_under_sudo() -> None:
    # Entering sudo writes REBAC's mandatory bypass audit row.
    memo, row = ReaderMemo("_test_answers"), SimpleNamespace()
    memo.remember(row, "graph", ALICE, True)
    with system_context(reason="test.reader_memo"):
        assert memo.get(row, "graph", ALICE) is None
    assert memo.get(row, "graph", ALICE) is True


def test_instance_prefetch_owns_the_protocol_tuple_and_the_hint_reader() -> None:
    seen: list[Any] = []

    class Pairs(InstancePrefetch):
        def related(self, instances: list[Any], queryset: Any) -> Any:
            seen.append(self.reader(queryset))
            return instances, id, id

    class Owner:
        pairs = Pairs()

    rows = [object()]
    with actor_context(ALICE):
        related, row_key, instance_key, single, name, descriptor = Owner.pairs.get_prefetch_querysets(rows, [object()])
    assert (related, single, name, descriptor) == (rows, True, "pairs", False)
    assert row_key(rows[0]) == instance_key(rows[0])
    assert seen == [ALICE]
    with pytest.raises(ValueError, match="requires one queryset and to_attr"):
        Owner.pairs.get_prefetch_querysets(rows, None)
    # ``require_rebac_queryset(..., actor=None)`` empties the hint: it reads for no reader.
    with actor_context(ALICE):
        Owner.pairs.get_prefetch_querysets(rows, [get_user_model().objects.none()])
    assert seen == [ALICE, None]


def test_prefetched_builds_its_hint_only_for_a_row_no_list_read_prefetched() -> None:
    """A listed row answers from its attribute; only a miss builds the hint, which must name that attribute."""

    built: list[str] = []

    def hint(to_attr: str) -> Any:
        built.append(to_attr)
        return SimpleNamespace(to_attr=to_attr)

    listed = SimpleNamespace(_answer="listed")
    assert prefetched(listed, "_answer", lambda: hint("_answer")) == "listed"
    assert built == []
    with pytest.raises(ValueError, match="needs a hint to it"):
        prefetched(SimpleNamespace(), "_answer", lambda: hint("_other"))
    assert built == ["_other"]


def test_ordinary_prefetch_preserves_empty_answers_without_evaluating_querysets() -> None:
    """A cache hit never re-enters the queryset's authorization or fetch path."""

    class CachedQuery:
        def __init__(self, rows):
            self._result_cache = rows

        def __iter__(self):
            pytest.fail("A prefetch reader must use the already materialized answer")

    row = get_user_model()()
    assert prefetched_objects(row, "items") is None
    row._prefetched_objects_cache = {"items": CachedQuery([])}
    assert prefetched_objects(row, "items") == []
    rows = [object()]
    row._prefetched_objects_cache["items"] = CachedQuery(rows)
    assert prefetched_objects(row, "items") is rows
    assert prefetched_objects(row, "other") is None
