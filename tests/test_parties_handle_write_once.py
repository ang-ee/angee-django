"""A handle's ingestion keys are written once: only the handle owner's verbs change its platform, value or external id.

A held message reads its addresses from its handles when it is sent, so a field
write of one of them would send another person's message somewhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.messaging.testing.models import Handle, Message, Party, Recipient
from angee.provenance.context import changeset
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from tests.conftest import create_platform_admin, create_user, make_addon
from tests.messaging_campaign import grant, make_user
from tests.test_messaging_compose import DESK, _wire, desk  # noqa: F401 -- the desk fixture

KEYS = ("platform", "value", "external_id", "normalized_value")
REFUSAL = "This identity or receipt is immutable."
CHANGES = {"platform": Handle.Platform.OTHER, "value": "elsewhere@example.net", "external_id": "another-account"}


def _keys(handle: Any) -> tuple[Any, ...]:
    with system_context(reason="tests.parties.write_once.read"):
        return Handle._base_manager.values_list(*KEYS).get(pk=handle.pk)


def _keyed(platform: str, value: str, external_id: str = "") -> tuple[Any, ...]:
    """The keys a handle naming ``value`` stores, its comparison key as the rule gives it."""

    return platform, value, external_id, Handle.normalize_value(platform, value)


def _write(handle: Any, actor: Any, *, partial: bool = False, **changes: Any) -> None:
    """Write ``changes`` onto ``handle`` as ``actor``, as any code holding the row would: set the fields, save."""

    with actor_context(actor):
        row = Handle._base_manager.get(pk=handle.pk).with_actor(actor)
        for name, value in changes.items():
            setattr(row, name, value)
        row.save(update_fields=[*changes, "updated_at"] if partial else None)


def _refused(handle: Any, actor: Any, field: str, *, partial: bool = False) -> None:
    """``actor``'s write of ``field`` is the foundation's refusal for a write-once field, and nothing changes."""

    stored = _keys(handle)
    with pytest.raises(ValidationError) as refused:
        _write(handle, actor, partial=partial, **{field: CHANGES[field]})
    assert refused.value.messages == [REFUSAL]
    assert _keys(handle) == stored


@pytest.mark.parametrize("partial", [False, True], ids=["save", "save-of-the-field"])
@pytest.mark.parametrize("field", list(CHANGES))
def test_a_contacts_creator_cannot_readdress_it_and_another_persons_held_message_keeps_its_address(
    desk: Any, field: str, partial: bool,  # noqa: F811 -- the compose desk fixture
) -> None:
    """The first person to type an address holds its handle, and still cannot change what the handle names."""

    first, second = make_user("write-once-first"), make_user("write-once-second")
    for replier in (first, second):
        grant(desk, "replier", replier)
    Message.objects.compose(channel=desk, to=["client@example.test"], text="Hi.", actor=first)
    held = Message.objects.compose(channel=desk, to=["client@example.test"], text="For approval.", actor=second)
    with system_context(reason="tests.parties.write_once.contact"):
        contact = Recipient._base_manager.select_related("handle").get(message=held).handle
    assert contact.created_by_id == first.pk

    _refused(contact, first, field, partial=partial)
    assert (held.status, _wire(desk, held)["To"]) == ("draft", "client@example.test")


@pytest.mark.parametrize("field", list(CHANGES))
def test_an_administrator_cannot_readdress_a_channels_sender_and_a_held_message_keeps_its_sender(
    desk: Any, field: str,  # noqa: F811 -- the compose desk fixture
) -> None:
    """A held message keeps the sender it was written with: an administrator's field write is refused too."""

    administrator = create_platform_admin("write-once-sender-administrator")
    held = Message.objects.compose(channel=desk, to=["client@example.test"], text="Held.", actor=desk.owner)
    with system_context(reason="tests.parties.write_once.sender"):
        sender = Handle._base_manager.get(pk=held.sender_id)

    _refused(sender, administrator, field)
    assert _wire(desk, held)["From"] == DESK


FIELDS = ("display_name", *CHANGES)
DECISIONS = {
    # seat: whether its write lands, for each of FIELDS
    "creator": (True, False, False, False),
    "contact manager": (True, False, False, False),
    "administrator": (True, False, False, False),
    "outsider": (False, False, False, False),
}


@pytest.fixture
def seats(db: None) -> tuple[Any, dict[str, Any]]:
    """A handle its party holds; its creator, a contact manager (through the party), an administrator, an outsider."""

    del db
    creator = create_user("write-once-creator")
    manager = create_user("write-once-contact-manager")
    with system_context(reason="tests.parties.write_once.seats"):
        party = Party._base_manager.create(display_name="Client Example", created_by=creator)
        handle = Handle.objects.upsert(platform="email", value="client@example.test", created_by_id=creator.pk)
        Party.objects.assert_handle(party, handle, asserted_by=creator)
        grant_role(actor=manager, role="parties/role:contact_manager")
    assert Handle._base_manager.get(pk=handle.pk).party_id == party.pk
    actors = {
        "creator": creator,
        "contact manager": manager,
        "administrator": create_platform_admin("write-once-administrator"),
        "outsider": create_user("write-once-outsider"),
    }
    return handle, actors


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("seat", list(DECISIONS))
def test_who_may_change_what_on_a_handle(seats: tuple[Any, dict[str, Any]], seat: str, field: str) -> None:
    """Whoever holds ``write`` changes a handle's display fields; nobody changes its ingestion keys by a field write."""

    handle, actors = seats
    change = "Client" if field == "display_name" else CHANGES[field]
    before = Handle._base_manager.values(*FIELDS).get(pk=handle.pk)
    if DECISIONS[seat][FIELDS.index(field)]:
        _write(handle, actors[seat], **{field: change})
        assert Handle._base_manager.values_list(field, flat=True).get(pk=handle.pk) == change
        return
    with pytest.raises((ValidationError, PermissionDenied)) as refused:
        _write(handle, actors[seat], **{field: change})
    if seat != "outsider":
        assert isinstance(refused.value, ValidationError) and refused.value.messages == [REFUSAL]
    assert Handle._base_manager.values(*FIELDS).get(pk=handle.pk) == before


@pytest.mark.parametrize("field", list(CHANGES))
def test_a_refused_write_is_keyed_to_the_field_it_refuses(seats: tuple[Any, dict[str, Any]], field: str) -> None:
    """The refusal names the field, so a form shows it beside the field it refuses."""

    handle, actors = seats
    with pytest.raises(ValidationError) as refused:
        _write(handle, actors["creator"], **{field: CHANGES[field]})
    assert refused.value.message_dict == {field: [REFUSAL]}


def test_the_handle_owners_verbs_change_its_keys_and_the_comparison_key_follows(
    db: None, django_capture_on_commit_callbacks: Any,
) -> None:
    """``upsert`` behind a matched external id and ``reidentify`` change what a handle names; a fold changes no row's
    keys; reverting a rewrite restores them. Each verb's allowance is spent by its own save."""

    del db
    with system_context(reason="tests.parties.write_once.upsert"):
        created = Handle.objects.upsert(platform="whatsapp", value="+14155552671", external_id="account-1")
        assert _keys(created) == _keyed("whatsapp", "+14155552671", "account-1")
        # The account behind the source's stable id moved to another number: its row follows it.
        moved = Handle.objects.upsert(platform="whatsapp", value="+14155552672", external_id="account-1")
        same = Handle.objects.upsert(platform="whatsapp", value="+14155552672", external_id="account-1")
        assert created.pk == moved.pk == same.pk
        assert _keys(created) == _keyed("whatsapp", "+14155552672", "account-1")
        # Nothing the verb returns carries its allowance, whether its save ran or nothing was dirty.
        for row in (moved, same):
            row.value = "+14155552673"
            with pytest.raises(ValidationError, match="immutable"):
                row.save()
        assert _keys(created) == _keyed("whatsapp", "+14155552672", "account-1")

        lid = Handle.objects.upsert(platform="whatsapp", value="111@lid", external_id="111@lid")
        alias = Handle.objects.upsert(platform="whatsapp", value="222@lid", external_id="222@lid")
    rewritten = "tests.parties.write_once.reidentify"
    with (
        django_capture_on_commit_callbacks(execute=True),
        changeset(fresh=True, change_reason=rewritten),
        system_context(reason=rewritten),
    ):
        assert Handle.objects.reidentify(lid, expected_value="111@lid", value="+14155552674") == (False, False)
    assert _keys(lid) == _keyed("whatsapp", "+14155552674", "111@lid")
    with system_context(reason="tests.parties.write_once.fold"):
        assert Handle.objects.reidentify(alias, expected_value="222@lid", value="+14155552674") == (True, False)
        assert Handle._base_manager.get(pk=alias.pk).merged_into_id == lid.pk
    assert _keys(alias) == _keyed("whatsapp", "222@lid", "222@lid")
    assert _keys(lid) == _keyed("whatsapp", "+14155552674", "111@lid")

    administrator = create_platform_admin("write-once-reverter")
    with system_context(reason="tests.parties.write_once.boundary"):
        rewrite = Handle.history.filter(id=lid.pk, changeset__change_reason=rewritten).latest("history_id").changeset
    with actor_context(administrator):
        rewrite.with_actor(administrator).revert()
    assert _keys(lid) == _keyed("whatsapp", "111@lid", "111@lid")


def test_an_account_id_given_as_a_number_is_the_id_its_row_stores_as_text(db: None) -> None:
    """A source that gives its account id as a number lands on the row holding that id, changing nothing."""

    del db
    with system_context(reason="tests.parties.write_once.numeric_id"):
        held = Handle.objects.upsert(platform="telegram", value="@someone", external_id="424242")
        again = Handle.objects.upsert(platform="telegram", value="@someone", external_id=424242)
    assert again.pk == held.pk
    assert _keys(held) == _keyed("telegram", "@someone", "424242")


def test_an_address_a_merged_spelling_holds_lands_on_the_row_its_account_id_names(db: None) -> None:
    """The id's row keeps its address when the new one is a spelling merged into it: nothing collides or moves."""

    del db
    with system_context(reason="tests.parties.write_once.merged_spelling"):
        account = Handle.objects.upsert(platform="whatsapp", value="+14155552681", external_id="acct-b")
        spelling = Handle.objects.upsert(platform="whatsapp", value="+14155552682")
        account.fold([spelling])
        landed = Handle.objects.upsert(platform="whatsapp", value="+14155552682", external_id="acct-b")
        assert Handle._base_manager.filter(pk=account.pk).exists()  # The caller's transaction is still usable.
    assert landed.pk == account.pk
    assert _keys(account) == _keyed("whatsapp", "+14155552681", "acct-b")
    assert _keys(spelling) == _keyed("whatsapp", "+14155552682")
    assert Handle._base_manager.get(pk=spelling.pk).merged_into_id == account.pk


def test_later_messages_from_an_account_whose_new_address_a_merged_spelling_holds_write_nothing(db: None) -> None:
    """Each lands on the account's row as it is: no update, no history version, no new stamp."""

    del db
    with system_context(reason="tests.parties.write_once.merged_spelling_repeats"):
        account = Handle.objects.upsert(platform="whatsapp", value="+14155552685", external_id="acct-c")
        spelling = Handle.objects.upsert(platform="whatsapp", value="+14155552686")
        account.fold([spelling])
        Handle.objects.upsert(platform="whatsapp", value="+14155552686", external_id="acct-c")
        versions = Handle.history.filter(id=account.pk).count()
        stamped = Handle._base_manager.get(pk=account.pk).updated_at
        counts = []
        for _ in range(2):
            with CaptureQueriesContext(connection) as queries:
                landed = Handle.objects.upsert(platform="whatsapp", value="+14155552686", external_id="acct-c")
            assert landed.pk == account.pk
            statements = [query["sql"].lstrip().upper() for query in queries.captured_queries]
            assert not [sql for sql in statements if sql.startswith(("INSERT", "UPDATE"))], statements
            counts.append(len(queries))
    assert counts[0] == counts[1]
    assert Handle.history.filter(id=account.pk).count() == versions
    assert Handle._base_manager.get(pk=account.pk).updated_at == stamped
    assert _keys(account) == _keyed("whatsapp", "+14155552685", "acct-c")


def test_an_upsert_with_nothing_to_change_runs_one_statement_and_only_a_moved_address_reads_who_holds_it(
    db: None,
) -> None:
    """The read of who holds a moved address runs on the move alone: a call that changes nothing stays one read."""

    del db
    unchanged = (
        {"platform": "email", "value": "budget@example.test", "display_name": "B"},
        {"platform": "whatsapp", "value": "+14155552691", "external_id": "acct-h", "display_name": "W"},
        {"platform": "telegram", "value": "@budget", "external_id": 515151},
    )
    with system_context(reason="tests.parties.write_once.statements"):
        for values in unchanged:
            Handle.objects.upsert(**{**values, "external_id": str(values.get("external_id", ""))})
        for values in unchanged:
            with CaptureQueriesContext(connection) as queries:
                Handle.objects.upsert(**values)
            assert len(queries) == 1, [query["sql"] for query in queries.captured_queries]
        moved = Handle.objects.upsert(platform="whatsapp", value="+14155552692", external_id="acct-h")
    assert _keys(moved) == _keyed("whatsapp", "+14155552692", "acct-h")


HANDLE_SEED = "- _xref: client\n  platform: email\n  value: client@example.test\n"


def test_a_seed_creates_the_handle_it_names_and_cannot_readdress_it(db: None, tmp_path: Path) -> None:
    """A load creates the handle its row names; a later load of that row with another address is refused."""

    del db
    (tmp_path / "resources").mkdir()
    seed = tmp_path / "resources" / "020_parties.handle.yaml"
    seed.write_text(HANDLE_SEED)
    addon = make_addon(
        name="tests.write_once_seed", label="write_once_seed", path=tmp_path, resources={"install": [seed.name]},
    )
    Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="tests.parties.write_once.seed"):
        handle = Handle._base_manager.get(value="client@example.test")
    assert _keys(handle) == _keyed("email", "client@example.test")

    seed.write_text(HANDLE_SEED.replace("client@", "elsewhere@"))
    with pytest.raises(ResourceLoadError, match=r"\{'value': \['This identity or receipt is immutable.'\]\}"):
        Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    assert _keys(handle) == _keyed("email", "client@example.test")
    assert not Handle._base_manager.filter(value="elsewhere@example.test").exists()
