"""A handle's ingestion keys are written once: only the handle owner's verbs change its platform, value or external id.

A held message reads its addresses from its handles when it is sent, so a field
write of one of them would send another person's message somewhere else. A merge
sends every later message to the merged address to the kept one, so a person
merges another spelling or alias only, unless they are a contact administrator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.base.models.identity import public_id_of
from angee.base.models.merge import MergeableMixin
from angee.graphql import records
from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.testing.models import Handle, Message, Party, Recipient
from angee.parties import schema as parties_schema
from angee.provenance.context import changeset
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from tests.conftest import addon_schema, create_platform_admin, create_user, make_addon
from tests.messaging_campaign import grant, make_user
from tests.test_messaging_compose import DESK, _wire, desk  # noqa: F401 -- the desk fixture

KEYS = ("platform", "value", "external_id", "spelling_key", "normalized_value")
REFUSAL = "This identity or receipt is immutable."
CHANGES = {"platform": Handle.Platform.OTHER, "value": "elsewhere@example.net", "external_id": "another-account"}


def _keys(handle: Any) -> tuple[Any, ...]:
    with system_context(reason="tests.parties.write_once.read"):
        return Handle._base_manager.values_list(*KEYS).get(pk=handle.pk)


def _keyed(platform: str, value: str, external_id: str = "") -> tuple[Any, ...]:
    """The keys a handle naming ``value`` stores, its spelling and person-matching keys as the rule gives them."""

    keys = Handle.spelling_key_of(platform, value), Handle.normalize_value(platform, value)
    return platform, value, external_id, *keys


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


def test_the_handle_owners_verbs_change_its_keys_and_the_derived_keys_follow(
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
SPELLED_SEED = "- _xref: client_again\n  platform: email\n  value: Client@Example.test\n"


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


@pytest.mark.parametrize(
    ("numbers", "lands"),
    [(("+1 787 555 0100", "+17875550100"), True), (("1-800-FLOWERS", "1-800-COLLECT"), False)],
    ids=["two spellings of one number", "two numbers the rule reads only as digits"],
)
def test_a_person_merges_two_spellings_of_a_number_and_never_two_numbers_that_share_only_their_digits(
    db: None, numbers: tuple[str, str], lands: bool,
) -> None:
    """The phone rule's fallback key, a value's digits (both of these are ``1800``), names no one address."""

    del db
    creator = create_user("merge-number-creator")
    with system_context(reason="tests.parties.write_once.numbers"):
        kept, merged = (
            Handle.objects.upsert(platform="phone", value=number, created_by_id=creator.pk) for number in numbers
        )
    assert Handle.normalize_value("phone", kept.value) == Handle.normalize_value("phone", merged.value)
    with actor_context(creator):
        merge = Handle._base_manager.get(pk=kept.pk).with_actor(creator)
        if lands:
            merge.merge(records=[public_id_of(merged)])
        else:
            with pytest.raises(ValidationError) as refused:
                merge.merge(records=[public_id_of(merged)])
            assert refused.value.messages == [MERGE_REFUSAL]
    assert Handle._base_manager.get(pk=merged.pk).merged_into_id == (kept.pk if lands else None)


def test_a_seed_that_would_keep_a_second_live_handle_of_an_address_stops_its_load_and_writes_nothing(
    db: None, tmp_path: Path,
) -> None:
    """Another spelling of a seeded address under its own xref is refused as a repeated exact value is."""

    del db
    (tmp_path / "resources").mkdir()
    seed = tmp_path / "resources" / "020_parties.handle.yaml"
    seed.write_text(HANDLE_SEED + SPELLED_SEED)
    addon = make_addon(
        name="tests.write_once_spelling", label="write_once_spelling", path=tmp_path,
        resources={"install": [seed.name]},
    )
    with pytest.raises(ResourceLoadError, match="is this address; a second live handle of it is not kept"):
        Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="tests.parties.write_once.seed_spelling"):
        assert not Handle._base_manager.exists()

    # The row loaded alone; then a second spelling joins the file, to apply over what the first load wrote.
    seed.write_text(HANDLE_SEED)
    Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    seed.write_text(HANDLE_SEED + SPELLED_SEED)
    with pytest.raises(ResourceLoadError, match="is this address; a second live handle of it is not kept"):
        Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="tests.parties.write_once.seed_spelling"):
        assert list(Handle._base_manager.values_list("value", flat=True)) == ["client@example.test"]

    # A re-apply: the seeded row as the repair has not reached it yet, beside a keyed spelling of its address; its
    # seed then changes, so the load saves it again.
    seed.write_text(HANDLE_SEED)
    with system_context(reason="tests.parties.write_once.seed_spelling"):
        seeded = Handle._base_manager.get(value="client@example.test")
        Handle._base_manager.filter(pk=seeded.pk).update(spelling_key="", normalized_version=1)
        keyed = Handle.objects.upsert(platform="email", value="CLIENT@example.test")
    seed.write_text(HANDLE_SEED + "  label: work\n")
    with pytest.raises(ResourceLoadError, match="is this address; a second live handle of it is not kept"):
        Resource.objects.load_addons((addon,), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="tests.parties.write_once.seed_spelling"):
        assert Handle._base_manager.values_list("label", "spelling_key").get(pk=seeded.pk) == ("", "")
        assert keyed.pk != seeded.pk


MERGE_REFUSAL = "Only a contact administrator merges two different addresses; merge their contacts instead."
MERGES = {
    # seat: whether its merge of a handle into the kept one lands, for (an alias, another address)
    "creator": (True, False),
    "contact manager": (False, False),
    "contact administrator": (True, True),
    "platform administrator": (True, True),
    "outsider": (False, False),
    "system fold": (True, True),
}
PAIRS = ("an alias", "another address")


@pytest.fixture
def merging(db: None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Three handles one contact holds, its creator's: the kept address, an alias of it and another address.

    The alias is a provider's (a Gmail dot), the same person-matching key: another letter case of the kept address
    is the kept handle itself.
    """

    del db
    creator = create_user("merge-creator")
    manager = create_user("merge-contact-manager")
    contact_admin = create_user("merge-contact-administrator")
    with system_context(reason="tests.parties.write_once.merging"):
        party = Party._base_manager.create(display_name="Client Example", created_by=creator)
        handles = {
            name: Handle.objects.upsert(platform="email", value=value, created_by_id=creator.pk)
            for name, value in (
                ("kept", "client.example@gmail.com"),
                ("an alias", "clientexample@gmail.com"),
                ("another address", "other@example.test"),
            )
        }
        for handle in handles.values():
            Party.objects.assert_handle(party, handle, asserted_by=creator)
        grant_role(actor=manager, role="parties/role:contact_manager")
        grant_role(actor=contact_admin, role="parties/role:contact_admin")
    actors = {
        "creator": creator,
        "contact manager": manager,
        "contact administrator": contact_admin,
        "platform administrator": create_platform_admin("merge-platform-administrator"),
        "outsider": create_user("merge-outsider"),
    }
    return handles, actors


@pytest.mark.parametrize("keys", ["keyed", "behind the repair"])
@pytest.mark.parametrize("pair", PAIRS)
@pytest.mark.parametrize("seat", list(MERGES))
def test_who_may_merge_which_handles(
    merging: tuple[dict[str, Any], dict[str, Any]], seat: str, pair: str, keys: str,
) -> None:
    """A person merges another spelling or alias into a handle; only a contact administrator merges another address.

    The rule computes each person-matching key from the value, so rows whose stored keys the repair has not reached
    yet (stale, each its own) decide as they will once it has.
    """

    handles, actors = merging
    kept, merged = handles["kept"], handles[pair]
    if keys == "behind the repair":
        with system_context(reason="tests.parties.write_once.behind"):
            for handle in (kept, merged):
                Handle._base_manager.filter(pk=handle.pk).update(
                    normalized_value=f"stale-{handle.pk}", spelling_key="", normalized_version=1,
                )
    stored = (_keys(kept), _keys(merged))

    def merge() -> None:
        if seat == "system fold":
            with system_context(reason="tests.parties.write_once.fold"):
                Handle._base_manager.get(pk=kept.pk).fold([Handle._base_manager.get(pk=merged.pk)])
            return
        actor = actors[seat]
        with actor_context(actor):
            Handle._base_manager.get(pk=kept.pk).with_actor(actor).merge(records=[public_id_of(merged)])

    if MERGES[seat][PAIRS.index(pair)]:
        merge()
        assert Handle._base_manager.get(pk=merged.pk).merged_into_id == kept.pk
    else:
        with pytest.raises((ValidationError, PermissionDenied)) as refused:
            merge()
        if seat == "creator":
            assert isinstance(refused.value, ValidationError) and refused.value.messages == [MERGE_REFUSAL]
        assert Handle._base_manager.get(pk=merged.pk).merged_into_id is None
    assert (_keys(kept), _keys(merged)) == stored


def _resolve_first(self: Any, merged: Any) -> None:
    """``Handle.retire_merged`` as it ran before: both parties resolved, then the redirect written."""

    merged.resolve()
    self.resolve()
    MergeableMixin.retire_merged(self, merged)


@pytest.mark.parametrize("order", ["redirect first", "resolve first"])
@pytest.mark.parametrize("verb", ["a person's merge", "a system fold"])
def test_a_handle_merge_ends_in_the_same_rows_whichever_order_and_only_the_history_between_differs(
    merging: tuple[dict[str, Any], dict[str, Any]], monkeypatch: pytest.MonkeyPatch, verb: str, order: str,
) -> None:
    """Redirecting before resolving changes only the merged handle's version between: the rows it ends in agree."""

    handles, actors = merging
    kept, merged = handles["kept"], handles["an alias"]
    party = Handle._base_manager.get(pk=kept.pk).party_id
    confirmed = Handle._base_manager.get(pk=kept.pk).party_link_confirmed
    if order == "resolve first":
        monkeypatch.setattr(Handle, "retire_merged", _resolve_first)
    if verb == "a system fold":
        with system_context(reason="tests.parties.write_once.order"):
            Handle._base_manager.get(pk=kept.pk).fold([Handle._base_manager.get(pk=merged.pk)])
    else:
        with actor_context(actors["creator"]):
            Handle._base_manager.get(pk=kept.pk).with_actor(actors["creator"]).merge(records=[public_id_of(merged)])

    rows = Handle._base_manager.filter(pk__in=(kept.pk, merged.pk)).values_list(
        "pk", "merged_into_id", "party_id", "party_link_confirmed",
    )
    assert set(rows) == {(kept.pk, None, party, confirmed), (merged.pk, kept.pk, None, False)}
    assert Party._base_manager.get(pk=party).handle_count == 2
    between = list(Handle.history.filter(id=merged.pk).order_by("history_id").values_list("merged_into_id", "party_id"))
    assert between[-2:] == (
        [(kept.pk, party), (kept.pk, None)] if order == "redirect first" else [(None, None), (kept.pk, None)]
    )


_MERGE = """mutation Merge($type: String!, $id: ID!, $records: [ID!]!) {
  merge_records(target_type: $type, target_id: $id, records: $records, confirm: true) {
    ok message validation_errors
  }
}"""


def _merge_records(user: Any, kept: Any, merged: Any) -> dict[str, Any]:
    """Merge ``merged`` into ``kept`` as ``user`` through the public ``merge_records`` mutation."""

    schema = addon_schema(parties_schema.schemas, "console", records.schemas)
    variables = {"type": "parties/handle", "id": public_id_of(kept), "records": [public_id_of(merged)]}
    return cast(dict[str, Any], result_data(execute_schema(schema, _MERGE, variables, user=user))["merge_records"])


def _two_contacts_one_replier_typed(
    channel: Any, client_address: str = "client@example.test",
) -> tuple[Any, Any, Any, Any]:
    """Two repliers; the first typed both the client's address and their own, so created both handles."""

    first, second = make_user("merge-redirect-first"), make_user("merge-redirect-second")
    for replier in (first, second):
        grant(channel, "replier", replier)
    Message.objects.compose(channel=channel, to=[client_address], text="Hi.", actor=first)
    Message.objects.compose(channel=channel, to=["mine@example.net"], text="Mine.", actor=first)
    with system_context(reason="tests.parties.write_once.redirect"):
        client = Handle._base_manager.get(value=client_address)
        mine = Handle._base_manager.get(value="mine@example.net")
    assert client.created_by_id == mine.created_by_id == first.pk
    return first, second, client, mine


def test_a_creator_cannot_merge_a_contact_into_their_own_address_and_the_next_persons_mail_goes_to_the_contact(
    desk: Any,  # noqa: F811 -- the compose desk fixture
) -> None:
    first, second, client, mine = _two_contacts_one_replier_typed(desk)
    held = Message.objects.compose(channel=desk, to=["client@example.test"], text="For approval.", actor=second)

    refused = _merge_records(first, mine, client)
    assert refused["ok"] is False and MERGE_REFUSAL in refused["message"], refused
    later = Message.objects.compose(channel=desk, to=["client@example.test"], text="Later.", actor=second)
    assert _wire(desk, held)["To"] == _wire(desk, later)["To"] == "client@example.test"
    assert Handle._base_manager.get(pk=client.pk).merged_into_id is None


@pytest.mark.parametrize(
    ("client_address", "kept_address"),
    [("client.example@gmail.com", "clientexample@gmail.com"), ("client@example.test", "mine@example.net")],
    ids=["an alias", "another address"],
)
def test_a_contact_administrator_merges_through_the_mutation_and_only_later_mail_follows_the_kept_handle(
    desk: Any, client_address: str, kept_address: str,  # noqa: F811 -- the compose desk fixture
) -> None:
    """A contact administrator merges the client into a handle they did not create; mail already written stays."""

    _first, second, client, mine = _two_contacts_one_replier_typed(desk, client_address)
    contact_admin = create_user("merge-redirect-contact-administrator")
    with system_context(reason="tests.parties.write_once.redirect"):
        kept = mine if kept_address == mine.value else Handle.objects.upsert(platform="email", value=kept_address)
        grant_role(actor=contact_admin, role="parties/role:contact_admin")
        party = Party._base_manager.create(display_name="Client Example", created_by=contact_admin)
        for handle in (client, kept):
            Party.objects.assert_handle(party, handle, asserted_by=contact_admin)
    held = Message.objects.compose(channel=desk, to=[client_address], text="For approval.", actor=second)

    assert _merge_records(contact_admin, kept, client)["ok"] is True
    later = Message.objects.compose(channel=desk, to=[client_address], text="Later.", actor=second)
    assert _wire(desk, held)["To"] == client_address
    assert _wire(desk, later)["To"] == kept_address
    assert Handle._base_manager.get(pk=client.pk).merged_into_id == kept.pk
    assert _keys(client) == _keyed("email", client_address)


def test_merging_two_contacts_leaves_each_of_their_addresses_as_it_is(
    desk: Any, django_capture_on_commit_callbacks: Any,  # noqa: F811 -- the compose desk fixture
) -> None:
    """Saying two addresses reach one person merges their contacts, and moves no address and no mail."""

    owner = desk.owner
    with system_context(reason="tests.parties.write_once.contacts"):
        kept, merged = (
            Party._base_manager.create(display_name=name, created_by=owner) for name in ("Ada", "Ada Work")
        )
        home = Handle.objects.upsert(platform="email", value="ada@example.test", created_by_id=owner.pk)
        work = Handle.objects.upsert(platform="email", value="ada@work.example", created_by_id=owner.pk)
        Party.objects.assert_handle(kept, home, asserted_by=owner)
        Party.objects.assert_handle(merged, work, asserted_by=owner)
    with actor_context(owner), django_capture_on_commit_callbacks(execute=True):
        kept.with_actor(owner).merge(records=[merged.public_id])

    with system_context(reason="tests.parties.write_once.contacts"):
        rows = dict(Handle._base_manager.filter(pk__in=(home.pk, work.pk)).values_list("value", "merged_into_id"))
    assert rows == {"ada@example.test": None, "ada@work.example": None}
    later = Message.objects.compose(channel=desk, to=["ada@work.example"], text="Hi.", actor=owner)
    assert _wire(desk, later)["To"] == "ada@work.example"
