"""The resource ledger owns its target-model compatibility and its writes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from django.apps import apps
from django.db import close_old_connections, connection, connections
from rebac import system_context

from angee.resources.entries import ResourceEntry
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import ContentTypeRow, Resource
from tests.conftest import create_platform_admin
from tests.mtidemo.models import MtiChild, MtiParent


def test_ledger_row_admits_its_model_or_a_recorded_multi_table_parent() -> None:
    """A row moved onto a child keeps its parent's ledger; any other model is a collision."""

    ledger = Resource(source_addon="angee.iam", xref="seed", target_model=MtiParent._meta.label)
    ledger.require_target_model(MtiParent)
    ledger.require_target_model(MtiChild)
    with pytest.raises(ResourceLoadError, match="xref collision in angee.iam: 'seed' already targets"):
        ledger.require_target_model(ContentTypeRow)


@pytest.mark.usefixtures("composed_tables")
def test_bound_identity_stays_until_an_owner_replaces_it() -> None:
    """Binding is idempotent; another record needs ``replace``, and the ledger then names it."""

    first = create_platform_admin("ledger-first-owner")
    second = create_platform_admin("ledger-second-owner")
    iam = apps.get_app_config("iam")
    with system_context(reason="test resource ledger binding"):
        Resource.objects.bind_instance(addon=iam, xref=" owner ", instance=first, source="test")
        Resource.objects.bind_instance(addon=iam, xref="owner", instance=first, source="test")
        with pytest.raises(ResourceLoadError, match="already identifies another record"):
            Resource.objects.bind_instance(addon=iam, xref="owner", instance=second, source="test")
        assert Resource.objects.resolve("iam.owner").pk == first.pk
        Resource.objects.bind_instance(addon=iam, xref="owner", instance=second, source="test", replace=True)
        assert Resource.objects.resolve("iam.owner").pk == second.pk
        assert Resource._base_manager.filter(source_addon=iam.name, xref="owner").count() == 1
        with pytest.raises(ResourceLoadError, match="saved target"):
            Resource.objects.bind_instance(addon=iam, xref="unsaved", instance=type(first)(), source="test")


def _entry() -> ResourceEntry:
    return ResourceEntry(addon=apps.get_app_config("iam"), tier="install", source_value="ledger")


@pytest.mark.usefixtures("composed_tables")
def test_upsert_refuses_a_row_bound_to_another_model_before_writing() -> None:
    """A compatible row takes the new target; an incompatible one is refused and left as it was."""

    owner = create_platform_admin("ledger-upsert-owner")
    with system_context(reason="test resource ledger upsert"):
        row = ContentTypeRow.objects.create(name="seeded")
        ledger = Resource.objects.upsert_target(
            entry=_entry(),
            xref="seed",
            instance=row,
            model=ContentTypeRow,
            content_hash="first",
        )
        again = Resource.objects.upsert_target(
            entry=_entry(),
            xref="seed",
            instance=row,
            model=ContentTypeRow,
            content_hash="second",
        )
        kept = Resource.objects.upsert_target(
            entry=_entry(),
            xref="seed",
            instance=row,
            model=ContentTypeRow,
            content_hash="third",
            replace=False,
        )
        with pytest.raises(ResourceLoadError, match="xref collision"):
            Resource.objects.upsert_target(
                entry=_entry(),
                xref="seed",
                instance=owner,
                model=type(owner),
                content_hash="other",
            )
        with pytest.raises(ResourceLoadError, match="xref collision"):
            Resource.objects.bind_instance(
                addon=apps.get_app_config("iam"),
                xref="seed",
                instance=owner,
                source="ledger",
                replace=True,
            )
        stored = Resource._base_manager.get(pk=ledger.pk)
    assert again.pk == kept.pk == ledger.pk
    assert (stored.target_model, stored.target_id, stored.content_hash) == (
        ContentTypeRow._meta.label,
        str(row.pk),
        "second",
    )


@pytest.mark.usefixtures("composed_tables")
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL resource ledger row-lock contract")
def test_concurrent_upserts_converge_on_one_row_and_refuse_another_model() -> None:
    """Two first loads of one xref leave one row; a concurrent load for another model is refused, not merged."""

    owner = create_platform_admin("ledger-concurrent-owner")
    with system_context(reason="test resource ledger concurrent upsert"):
        row = ContentTypeRow.objects.create(name="contested")

    def upsert(xref: str, target: Any, barrier: Barrier) -> str:
        close_old_connections()
        try:
            barrier.wait()
            with system_context(reason="test resource ledger concurrent upsert"):
                Resource.objects.upsert_target(
                    entry=_entry(),
                    xref=xref,
                    instance=target,
                    model=type(target),
                    content_hash="race",
                )
            return "stored"
        except ResourceLoadError:
            return "refused"
        finally:
            connections.close_all()

    for xref, targets in (("same-model", (row, row)), ("other-model", (row, owner))):
        barrier = Barrier(2, timeout=5)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(upsert, (xref, xref), targets, (barrier, barrier)))
        stored = Resource._base_manager.filter(xref=xref)
        assert stored.count() == 1
        if targets[0] is targets[1]:
            assert outcomes == ["stored", "stored"]
        else:
            assert sorted(outcomes) == ["refused", "stored"]
            winner = targets[outcomes.index("stored")]
            assert stored.get().target_model == type(winner)._meta.label
