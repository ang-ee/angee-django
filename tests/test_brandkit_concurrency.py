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
from rebac import system_context

from angee.base.models.identity import public_id_of
from angee.brandkit.testing.models import BrandKit
from angee.graphql.testing.documents import execute_schema
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
    """The console locks its subject before saving it; for the default kit that takes every kit, in key order, first.

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
