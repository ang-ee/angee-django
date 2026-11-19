"""A handle's holder is the projection of a statement: write paths state it, and the repair states stored holders."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import OutputWrapper
from django_compose.provision import ProvisionContext
from rebac import actor_context, system_context, to_subject_ref

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging.testing.models import Handle, Party
from angee.resources.testing.models import Resource
from tests.conftest import create_user, make_addon


def _stored_holder(owner: Any) -> tuple[Any, Any]:
    """A handle whose holder was written onto its row, with no statement behind it."""

    with system_context(reason="test stored holder"):
        holder = Party._base_manager.create(display_name="Ada Example", created_by=owner)
        handle = Handle._base_manager.create(
            platform="email", value="ada@example.com", party=holder, created_by=owner,
        )
    return holder, handle


def _claim(handle: Any, owner: Any) -> Any:
    """Another source's party claims the address below review confidence, as an importer does."""

    with system_context(reason="test claim"):
        claimant = Party._base_manager.create(display_name="Ada E.", created_by=owner)
        Party.objects.propose_claimed_handle_as_system(
            claimant, handle, evidence=handle, reason="tests.parties.claim",
        )
    return claimant


def _stated_links(handle: Any) -> list[tuple[Any, bool]]:
    """The stored-holder rule's statements on ``handle``: their subject and whether they decide it."""

    rule = str(to_subject_ref(Party.objects.rule_source("stored_holder")))
    rows = Party.statements.model._base_manager.filter(handle=handle, asserted_by=rule).order_by("pk")
    return [(row.subject_id, row.applied) for row in rows]


def test_a_stored_holder_keeps_its_address_when_a_claim_follows_the_repair(db: None) -> None:
    """The repair states the holder a row carries; a later claim below review confidence leaves it."""

    del db
    owner = create_user("stored-holder-owner")
    holder, handle = _stored_holder(owner)

    output = StringIO()
    call_command("resolve_stale_owners", stdout=output)
    _claim(handle, owner)

    handle.refresh_from_db()
    holder.refresh_from_db()
    assert (handle.party_id, holder.handle_count) == (holder.pk, 1)
    assert output.getvalue() == (
        "Stated 1 stored holder(s); left 0 confirmed and 0 contested holder(s) for a person; "
        "re-resolved 0 stale holder(s).\n"
    )
    assert _stated_links(handle) == [(holder.pk, True)]


def test_the_repair_states_a_stored_holder_once(db: None) -> None:
    """Running the repair again finds nothing left to state."""

    del db
    holder, handle = _stored_holder(create_user("stored-holder-repeat"))

    with system_context(reason="test repeated repair"):
        first = Handle.objects.resolve_stale_owners()
        second = Handle.objects.resolve_stale_owners()

    handle.refresh_from_db()
    assert (handle.party_id, first.stated, second) == (holder.pk, 1, (0, (), (), 0, 0))
    assert _stated_links(handle) == [(holder.pk, True)]


def _repair() -> Any:
    with system_context(reason="test holder repair"):
        return Handle.objects.resolve_stale_owners()


def test_the_repair_leaves_a_confirmed_holder_confirmed_and_reports_it(db: None) -> None:
    """A rule's normal statement would clear the confirmation a consumer's certain match reads: leave it."""

    del db
    holder, handle = _stored_holder(create_user("stored-holder-confirmed"))
    with system_context(reason="test confirmed holder"):
        Handle._base_manager.filter(pk=handle.pk).update(party_link_confirmed=True)

    repair = _repair()

    handle.refresh_from_db()
    assert (handle.party_id, handle.party_link_confirmed) == (holder.pk, True)
    assert (repair.stated, repair.confirmed, repair.contested, repair.resolved) == (0, (handle.pk,), (), 0)
    assert _stated_links(handle) == []


def test_the_repair_leaves_a_contested_holder_on_its_row_and_reports_it(db: None) -> None:
    """Stating a holder another party's statement outranks would hand that party the address: leave it."""

    del db
    owner = create_user("stored-holder-contested")
    holder, handle = _stored_holder(owner)
    with system_context(reason="test contested holder"):
        directory_card = Party._base_manager.create(display_name="A. Example", created_by=owner)
        Party.objects.assert_handle(directory_card, handle, asserted_by=owner)
        Handle._base_manager.filter(pk=handle.pk).update(party=holder)

    repair = _repair()

    handle.refresh_from_db()
    assert handle.party_id == holder.pk
    assert (repair.stated, repair.confirmed, repair.contested, repair.resolved) == (0, (), (handle.pk,), 0)


def test_the_repair_leaves_a_trashed_holders_address_for_its_restore(db: None) -> None:
    """Resolution ignores a trashed party, so stating its link would clear the row; the restored holder is stated."""

    del db
    holder, handle = _stored_holder(create_user("stored-holder-trashed"))
    with system_context(reason="test trashed holder"):
        Party._base_manager.get(pk=holder.pk).trash(reason="test")

    left = _repair()

    handle.refresh_from_db()
    assert (handle.party_id, left.stated, _stated_links(handle)) == (holder.pk, 0, [])
    with system_context(reason="test restored holder"):
        Party._base_manager.get(pk=holder.pk).restore()
    assert _repair().stated == 1
    handle.refresh_from_db()
    assert (handle.party_id, _stated_links(handle)) == (holder.pk, [(holder.pk, True)])


def test_the_repair_never_re_resolves_a_trashed_holders_address(db: None) -> None:
    """A trashed holder whose statements no longer decide it keeps its row until a restore."""

    del db
    owner = create_user("stale-holder-trashed")
    holder, handle = _stored_holder(owner)
    with system_context(reason="test stale trashed holder"):
        Party.objects.propose_claimed_handle_as_system(holder, handle, evidence=handle, reason="tests.parties.claim")
        Handle._base_manager.filter(pk=handle.pk).update(party=holder)
        Party._base_manager.get(pk=holder.pk).trash(reason="test")

    assert _repair().resolved == 0
    handle.refresh_from_db()
    assert handle.party_id == holder.pk


def _stored_holders(owner: Any, count: int) -> list[Any]:
    with system_context(reason="test stored holders"):
        return [
            Handle._base_manager.create(
                platform="email",
                value=f"holder-{index}@example.com",
                party=Party._base_manager.create(display_name=f"Holder {index}", created_by=owner),
                created_by=owner,
            )
            for index in range(count)
        ]


def test_the_repair_stops_at_its_cap_and_the_next_run_continues(db: None) -> None:
    """Each run writes at most ``limit`` holders and says how many it left; the next run takes them."""

    del db
    handles = _stored_holders(create_user("stored-holder-cap"), 3)
    with system_context(reason="test capped repair"):
        first = Handle.objects.resolve_stale_owners(limit=2)
        second = Handle.objects.resolve_stale_owners(limit=2)

    assert (first.stated, first.remaining, second.stated, second.remaining) == (2, 1, 1, 0)
    assert all(_stated_links(handle) for handle in handles)


def test_an_operator_sees_where_a_capped_repair_stopped(db: None) -> None:
    del db
    _stored_holders(create_user("stored-holder-cap-output"), 2)
    output = StringIO()

    call_command("resolve_stale_owners", limit=1, stdout=output)

    assert output.getvalue().splitlines()[-1] == (
        "Stopped at the cap of 1 holder(s); 1 more await the next provision or resolve_stale_owners run."
    )


def test_the_repair_states_a_merged_holder_for_its_survivor(db: None) -> None:
    del db
    owner = create_user("stored-holder-merged")
    holder, handle = _stored_holder(owner)
    with system_context(reason="test merged holder"):
        survivor = Party._base_manager.create(display_name="Ada E.", created_by=owner)
    with actor_context(owner):
        Party.objects.get(pk=survivor.pk).merge(records=[holder.public_id])
    with system_context(reason="test a holder written before the merge moved it"):
        Handle._base_manager.filter(pk=handle.pk).update(party=holder)

    assert _repair().stated == 1

    handle.refresh_from_db()
    assert (handle.party_id, _stated_links(handle)) == (survivor.pk, [(survivor.pk, True)])


def test_provisioning_runs_the_repair(db: None) -> None:
    """A deployment never depends on someone running the command: every provision repairs holders."""

    del db
    holder, handle = _stored_holder(create_user("stored-holder-provision"))
    output = StringIO()

    apps.get_app_config("parties").compose_provision(
        ProvisionContext("default", OutputWrapper(output), OutputWrapper(StringIO()))
    )

    assert "Stated 1 stored holder(s)" in output.getvalue()
    assert _stated_links(handle) == [(holder.pk, True)]


@pytest.mark.parametrize("field", ["party", "party_id", "party_link_confirmed"])
def test_handle_upsert_refuses_to_write_a_holder(db: None, field: str) -> None:
    """The contact-point write path never writes the holder a statement decides."""

    del db
    with system_context(reason="test holder upsert"):
        holder = Party._base_manager.create(display_name="Ada Example")
        value = {"party": holder, "party_id": holder.pk, "party_link_confirmed": True}[field]
        with pytest.raises(TypeError, match="assert_handle"):
            Handle.objects.upsert(platform="email", value="ada@example.com", **{field: value})
        assert not Handle._base_manager.filter(value="ada@example.com").exists()


HOLDER_SEED = {
    "010_parties.party.yaml": "- _xref: ada\n  display_name: Ada Example\n",
    "020_parties.handle.yaml": (
        "- _xref: ada_email\n  platform: email\n  value: ada@example.com\n  party: holder_seed.ada\n"
        "- _xref: bob_email\n  platform: email\n  value: bob@example.com\n"
    ),
}


def test_a_resource_row_naming_a_holder_states_it(db: None, tmp_path: Path) -> None:
    """A seeded holder is stated as the load commits, so a claim stated on it afterwards leaves it."""

    del db
    (tmp_path / "resources").mkdir()
    for name, body in HOLDER_SEED.items():
        (tmp_path / "resources" / name).write_text(body)
    addon = make_addon(
        name="tests.holder_seed", label="holder_seed", path=tmp_path, resources={"install": list(HOLDER_SEED)},
    )

    Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="test seeded holder"):
        handle = Handle._base_manager.get(value="ada@example.com")
        unheld = Handle._base_manager.get(value="bob@example.com")
    _claim(handle, create_user("seeded-holder-claimant"))

    handle.refresh_from_db()
    assert handle.party is not None and handle.party.display_name == "Ada Example"
    assert _stated_links(handle) == [(handle.party_id, True)]
    assert (unheld.party_id, _stated_links(unheld)) == (None, [])

    replay = Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    assert (replay.created, replay.updated) == (0, 0)
    assert _stated_links(handle) == [(handle.party_id, True)]


def test_a_holder_a_seed_moves_loses_the_statement_an_earlier_load_gave_it(db: None, tmp_path: Path) -> None:
    """Otherwise the old holder takes the address back, or stays linked beside the new one as one person."""

    del db
    seed = {
        "010_parties.party.yaml": (
            "- _xref: ada\n  display_name: Ada Example\n- _xref: bea\n  display_name: Bea Example\n"
        ),
        "020_parties.handle.yaml": (
            "- _xref: ada_email\n  platform: email\n  value: ada@example.com\n  party: moved_seed.ada\n"
        ),
    }
    (tmp_path / "resources").mkdir()
    for name, body in seed.items():
        (tmp_path / "resources" / name).write_text(body)
    addon = make_addon(
        name="tests.moved_seed", label="moved_seed", path=tmp_path, resources={"install": list(seed)},
    )
    Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="test moved seed"):
        handle = Handle._base_manager.get(value="ada@example.com")
        first = handle.party_id

    (tmp_path / "resources" / "020_parties.handle.yaml").write_text(
        seed["020_parties.handle.yaml"].replace("moved_seed.ada", "moved_seed.bea")
    )
    Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])

    handle.refresh_from_db()
    assert handle.party_id != first
    assert [subject for subject, applied in _stated_links(handle) if applied] == [handle.party_id]
    with system_context(reason="test moved seed"):
        rule = str(to_subject_ref(Party.objects.rule_source("stored_holder")))
        assert Party.statements.model._base_manager.get(
            handle=handle, subject_id=first, asserted_by=rule,
        ).status == "withdrawn"
        assert Party.objects.reconcile_same_as() == 0
