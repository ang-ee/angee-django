"""A handle's holder is the projection of a statement: write paths state it, and the repair states stored holders."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from rebac import system_context, to_subject_ref

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
    assert output.getvalue() == "Resolved 1 stale handle owner(s).\n"
    assert _stated_links(handle) == [(holder.pk, True)]


def test_the_repair_states_a_stored_holder_once(db: None) -> None:
    """Running the repair again finds nothing left to state."""

    del db
    holder, handle = _stored_holder(create_user("stored-holder-repeat"))

    with system_context(reason="test repeated repair"):
        first = Handle.objects.resolve_stale_owners()
        second = Handle.objects.resolve_stale_owners()

    handle.refresh_from_db()
    assert (handle.party_id, first, second) == (holder.pk, 1, 0)
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
