"""PostgreSQL interleavings of brand kit writes around the default kit.

A change of default locks every kit first, in key order, so a second change waits
for the first and then reads its result; a delete and a console write lock the
same way before their own row. Row locks do nothing on SQLite, so this module runs
only on PostgreSQL.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from rebac import actor_context, system_context

from angee.base.models.identity import public_id_of
from angee.base.querysets import row_lock_queryset
from angee.brandkit.testing.models import BrandKit
from angee.graphql.testing.documents import execute_schema
from angee.provenance.context import changeset
from angee.resources.testing.models import Resource
from tests.conftest import create_platform_admin, make_addon
from tests.lock_waits import submit, wait_for_lock
from tests.test_brandkit import _UPDATE_KIT, ANGEE, _default_names, _kit, _schema

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]

type Writer = Callable[[Any, Path], tuple[Callable[[], object], str]]
"""Return a call that makes a kit the default through one writer, and the name of the kit it makes the default."""


def _promote(kit: Any, *, clean: bool = False) -> None:
    with system_context(reason="tests.brandkit.concurrency.promote"):
        stored = BrandKit.objects.get(pk=kit.pk)
        stored.is_default = True
        if clean:
            stored.full_clean()
        stored.save()


def _model_verb(kit: Any, tmp_path: Path) -> tuple[Callable[[], object], str]:
    del tmp_path
    return lambda: _promote(kit), kit.name


def _full_clean_then_save(kit: Any, tmp_path: Path) -> tuple[Callable[[], object], str]:
    del tmp_path
    return lambda: _promote(kit, clean=True), kit.name


def _console_update(kit: Any, tmp_path: Path) -> tuple[Callable[[], object], str]:
    del tmp_path
    admin = create_platform_admin("brandkit-concurrent-admin")

    def update() -> None:
        variables = {"id": public_id_of(kit), "set": {"is_default": True}}
        result = execute_schema(_schema(), _UPDATE_KIT, variables, user=admin)
        assert result.errors is None, result.errors

    return update, kit.name


def _resource_load(kit: Any, tmp_path: Path) -> tuple[Callable[[], object], str]:
    del kit
    name = "010_brandkit.brandkit.yaml"
    (tmp_path / "resources").mkdir()
    row = {**ANGEE, "xref": "loaded", "name": "Loaded", "is_default": True}
    (tmp_path / "resources" / name).write_text(yaml.safe_dump([row]))
    addon = make_addon(name="tests.brandkit_seed", label="brandkit_seed", path=tmp_path, resources={"install": [name]})
    return lambda: Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL]), "Loaded"


def _contend_with_a_change_of_default_part_way_through_its_lock(first: Any, contender: Callable[[], object]) -> None:
    """Hold the first kit, as a change of default does part-way through its lock, then take every other kit.

    ``contender`` starts once the first kit is held and must wait on a lock; then the
    holder takes the rest and commits. A contender that holds any other kit row by
    then closes a cycle, and PostgreSQL aborts one of the two.
    """

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="tests.brandkit.concurrency.hold"):
            list(row_lock_queryset(BrandKit).filter(pk=first.pk).values_list("pk", flat=True))
            future, pid = submit(pool, contender)
            wait_for_lock(pid, future)
            BrandKit.objects.lock_default()
        future.result(timeout=30)


@pytest.mark.parametrize(
    "writer",
    [
        pytest.param(_model_verb, id="model-verb"),
        pytest.param(_full_clean_then_save, id="full-clean-then-save"),
        pytest.param(_console_update, id="console-update"),
        pytest.param(_resource_load, id="resource-load"),
    ],
)
def test_a_second_change_of_default_waits_for_the_first_and_takes_the_default_from_it(
    writer: Writer, tmp_path: Path,
) -> None:
    """Both promotions succeed, the second after the first commits, and its kit is the one default."""

    _kit("Default")
    first = _kit("First")
    second = _kit("Second")
    promote_second, expected = writer(second, tmp_path)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            _promote(first)
            contender, pid = submit(pool, promote_second)
            wait_for_lock(pid, contender)
        contender.result(timeout=10)
    assert _default_names() == [expected]


def test_three_changes_of_default_at_once_leave_exactly_one_default() -> None:
    """Two promotions wait on a third; all three succeed, one after another, and one kit is the default."""

    _kit("Default")
    first, second, third = (_kit(name) for name in ("First", "Second", "Third"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        with transaction.atomic():
            _promote(first)
            contenders = [submit(pool, partial(_promote, kit)) for kit in (second, third)]
            for contender, pid in contenders:
                wait_for_lock(pid, contender)
        for contender, _pid in contenders:
            contender.result(timeout=10)
    defaults = _default_names()
    assert len(defaults) == 1
    assert defaults[0] in {"Second", "Third"}


def test_an_edit_of_the_default_kit_holding_its_subject_lock_is_not_deadlocked_by_a_change_of_default() -> None:
    """The console locks its subject before saving it, and that takes every kit, in key order, first.

    Holding only the later kit, the edit's save would wait on the earlier kit a
    change of default had locked while that change waited on the later kit.
    """

    earlier = _kit("Earlier")
    later = _kit("Later")
    _promote(later)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="tests.brandkit.concurrency.edit"):
            edited = BrandKit.objects.get(pk=later.pk).lock_subject()
            contender, pid = submit(pool, lambda: _promote(earlier))
            wait_for_lock(pid, contender)
            edited.name = "Later, renamed"
            edited.save()
        contender.result(timeout=10)
    assert _default_names() == ["Earlier"]


def test_a_delete_locking_two_kits_is_not_deadlocked_by_a_change_of_default() -> None:
    """A queryset delete locks each kit as it judges it; the first lock takes every kit, in key order, first.

    Holding only the later kit, the delete would wait on the earlier kit a change
    of default had locked while that change waited on the later kit.
    """

    _kit("Default")
    earlier = _kit("Earlier")
    later = _kit("Later")

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="tests.brandkit.concurrency.delete"):
            assert BrandKit.objects.get(pk=later.pk).lock_for_delete() is not None
            contender, pid = submit(pool, lambda: _promote(earlier))
            wait_for_lock(pid, contender)
            assert BrandKit.objects.get(pk=earlier.pk).lock_for_delete() is not None
        contender.result(timeout=10)
    assert _default_names() == ["Earlier"]


def test_a_console_edit_of_a_kit_that_becomes_the_default_while_it_waits_takes_the_kits_lock_first() -> None:
    """The console's subject lock takes every kit first, whatever the kit was when the console read it.

    Read before its lock, B is not the default; once its row is free, B's promotion
    has made it the default, so its save moves the default. Holding B's row and then
    asking for every kit would close a cycle with a promotion of A waiting on the
    first kit.
    """

    _kit("Default")
    a = _kit("A")
    b = _kit("B")
    admin = create_platform_admin("brandkit-concurrent-renamer")

    def rename_b() -> None:
        variables = {"id": public_id_of(b), "set": {"name": "B, renamed"}}
        result = execute_schema(_schema(), _UPDATE_KIT, variables, user=admin)
        assert result.errors is None, result.errors

    with ThreadPoolExecutor(max_workers=2) as pool:
        with transaction.atomic():
            _promote(b)
            rename, rename_pid = submit(pool, rename_b)
            wait_for_lock(rename_pid, rename)
            promotion, promotion_pid = submit(pool, partial(_promote, a))
            wait_for_lock(promotion_pid, promotion)
        rename.result(timeout=30)
        promotion.result(timeout=30)
    assert _default_names() == ["A"]
    with system_context(reason="tests.brandkit.concurrency.read"):
        assert BrandKit.objects.get(pk=b.pk).name == "B, renamed"


def test_a_revert_of_a_change_of_default_takes_the_kits_lock_before_any_kit() -> None:
    """A changeset revert locks each kit it restores through its subject lock, which takes every kit first.

    The revert locks its kits by ``str(pk)``, here the demoted kit first. That kit's
    snapshot is not the default, so a subject lock that decided from the snapshot
    held the kit and then asked for every kit, closing a cycle with a change of
    default that holds the first kit.
    """

    first = _kit("First")
    demoted, promoted = sorted((_kit("Second"), _kit("Third")), key=lambda kit: str(kit.pk))
    _promote(demoted)
    with changeset(fresh=True):
        _promote(promoted)
    admin = create_platform_admin("brandkit-concurrent-reverter")
    snapshots: Any = BrandKit.history  # django-simple-history's manager, which the policy object stands in for
    with system_context(reason="tests.brandkit.concurrency.read"):
        boundary = snapshots.filter(id=promoted.pk).order_by("-history_id").first().changeset

    def revert() -> None:
        with actor_context(admin):
            boundary.with_actor(admin).revert()

    _contend_with_a_change_of_default_part_way_through_its_lock(first, revert)
    assert _default_names() == [demoted.name]


def test_a_delete_waits_for_a_change_of_default_and_refuses_the_new_default() -> None:
    """Making B the default locks B; an ORM delete of B read before the change waits, then refuses it."""

    _kit("A")
    b = _kit("B")

    def delete_b() -> None:
        with system_context(reason="brandkit concurrency test delete"):
            BrandKit.objects.get(pk=b.pk).delete()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(), system_context(reason="brandkit concurrency test default"):
            kit = BrandKit.objects.get(pk=b.pk)
            kit.is_default = True
            kit.save()
            contender, pid = submit(pool, delete_b)
            wait_for_lock(pid, contender)
        with pytest.raises(ValidationError, match="Make another kit the default"):
            contender.result(timeout=10)
    assert _default_names() == ["B"]
    with system_context(reason="brandkit concurrency test read"):
        assert list(BrandKit.objects.order_by("name").values_list("name", flat=True)) == ["A", "B"]
