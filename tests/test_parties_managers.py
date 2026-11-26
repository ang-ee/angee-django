"""Exact party-name candidates and confirmed handle read scopes."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext, isolate_apps
from rebac import system_context

from angee.base.managers import AngeeManager
from angee.base.models.trash import ArchiveMixin, ArchiveQuerySet
from angee.messaging.testing.models import Handle, Organization, Party, Person
from angee.parties.managers import PartyQuerySet
from tests.tables import model_tables


@pytest.mark.parametrize("refresh", [False, True])
def test_handle_upsert_bounds_source_display_text(db: None, refresh: bool) -> None:
    """Create and refresh share the model-declared display-name and label limits."""

    del db
    with system_context(reason="test bounded handle text"):
        prior = Handle.objects.upsert(platform="email", value="bounded@example.test") if refresh else None
        fields = {name: "é" * (Handle._meta.get_field(name).max_length + 1) for name in ("display_name", "label")}
        handle = Handle.objects.upsert(platform="email", value="bounded@example.test", **fields)
        handle.refresh_from_db()
    if prior is not None:
        assert handle.pk == prior.pk
    for name, value in fields.items():
        assert getattr(handle, name) == value[: Handle._meta.get_field(name).max_length]


@pytest.mark.parametrize("verb", ["upsert", "clean_identity"])
@pytest.mark.parametrize("field", ["value", "external_id"])
def test_handle_upsert_refuses_oversized_identifiers(db: None, field: str, verb: str) -> None:
    """Unlandable identities raise field validation instead of changing their key, and the check writes nothing."""

    del db
    fields = {"value": "bounded@example.test", "external_id": "stable-id"}
    fields[field] = "x" * (Handle._meta.get_field(field).max_length + 1)
    with system_context(reason="test invalid handle identifiers"):
        with pytest.raises(ValidationError) as refused:
            getattr(Handle.objects, verb)(platform="email", **fields)
    assert list(refused.value.error_dict) == [field]
    assert refused.value.error_dict[field][0].code == "max_length"
    assert not Handle._base_manager.exists()


@pytest.mark.parametrize("verb", ["upsert", "clean_identity"])
def test_handle_upsert_refuses_normalization_that_exceeds_its_field(db: None, verb: str) -> None:
    """Unicode case normalization may expand an otherwise column-sized identity."""

    del db
    value = "İ" * Handle._meta.get_field("value").max_length
    with system_context(reason="test invalid normalized handle"):
        with pytest.raises(ValidationError) as refused:
            getattr(Handle.objects, verb)(platform="other", value=value)
    assert list(refused.value.error_dict) == ["normalized_value"]
    assert not Handle._base_manager.exists()


def test_clean_identity_returns_the_person_matching_key_upsert_stores(db: None) -> None:
    """The check and the write agree on the key, and the check alone writes no row."""

    del db
    key = Handle.objects.clean_identity(platform="email", value="Person@Example.test")
    assert not Handle._base_manager.exists()
    with system_context(reason="test handle person-matching key"):
        handle = Handle.objects.upsert(platform="email", value="Person@Example.test")
    assert handle.normalized_value == key


def test_a_fill_only_name_fills_a_blank_handle_and_never_renames_a_named_one(db: None) -> None:
    """A typed name lands on a new or nameless handle; a handle that has a name keeps it."""

    del db
    with system_context(reason="test fill-only handle names"):
        Handle.objects.upsert(platform="email", value="named@example.test", display_name="Kept")
        Handle.objects.upsert(platform="email", value="blank@example.test")
        for value in ("named@example.test", "blank@example.test", "new@example.test"):
            Handle.objects.upsert(platform="email", value=value, display_name="Typed", fill_only=("display_name",))
        filled = dict(Handle._base_manager.values_list("value", "display_name"))
        # Without fill_only, a source name still refreshes a named handle.
        Handle.objects.upsert(platform="email", value="named@example.test", display_name="Source")
    assert filled == {"named@example.test": "Kept", "blank@example.test": "Typed", "new@example.test": "Typed"}
    assert Handle._base_manager.get(value="named@example.test").display_name == "Source"


def test_find_identities_reads_a_batch_in_one_statement_with_each_arm_naming_its_platform(db: None) -> None:
    """A value one platform holds answers only for that platform, and every arm is served by the identity key.

    The statement is the plan's evidence: an arm without its platform cannot use
    ``(platform, value)``'s index, and PostgreSQL then scans the table on every upsert.
    """

    del db
    with system_context(reason="test batched handle lookup"):
        ada = Handle.objects.upsert(platform="telegram", value="ada")
        Handle.objects.upsert(platform="slack", value="ada")
        mail = Handle.objects.upsert(platform="email", value="ada@example.test")
        wanted = [("telegram", "ada"), ("email", "ada@example.test"), ("email", "ada"), ("telegram", "bo")]
        with CaptureQueriesContext(connection) as queries:
            found = Handle.objects.find_identities(wanted)
    assert found == {("telegram", "ada"): ada, ("email", "ada@example.test"): mail}
    (statement,) = queries.captured_queries
    assert statement["sql"].count('."platform" = ') == 2


SPELLINGS = [
    # (platform, spellings, the spelling key of each, the person-matching key of each, one handle)
    ("email", ("ada@example.test", "  Ada@Example.TEST ", "ADA@example.test"), ("ada@example.test",) * 3,
     ("ada@example.test",) * 3, True),
    ("email", ("ada@Bücher.example", "ada@xn--bcher-kva.example", "ada@XN--BCHER-KVA.example"),
     ("ada@bücher.example",) * 3, ("ada@bücher.example",) * 3, True),
    # IDNA 2003: a punycode label carrying ß does not read back, so it stays as typed.
    ("email", ("ada@xn--strae-oqa.example", "ada@straße.example"), ("ada@xn--strae-oqa.example", "ada@straße.example"),
     ("ada@xn--strae-oqa.example", "ada@straße.example"), False),
    ("email", ("a.b@gmail.com", "ab@gmail.com", "A.B+promo@GMail.com"),
     ("a.b@gmail.com", "ab@gmail.com", "a.b+promo@gmail.com"), ("ab@gmail.com",) * 3, False),
    ("email", ("ab@googlemail.com", "ab@gmail.com"), ("ab@googlemail.com", "ab@gmail.com"),
     ("ab@googlemail.com", "ab@gmail.com"), False),
    ("email", ("ada+news@example.test", "ada@example.test"), ("ada+news@example.test", "ada@example.test"),
     ("ada+news@example.test", "ada@example.test"), False),
    ("email", ('"Ada Example"@example.test', '"ADA EXAMPLE"@example.test'), ('"ada example"@example.test',) * 2,
     ('"ada example"@example.test',) * 2, True),
    ("email", ("jörg@example.test", "JÖRG@example.test"), ("jörg@example.test",) * 2, ("jörg@example.test",) * 2, True),
    # lower(), not casefold(): two addresses.
    ("email", ("Straße@example.test", "STRASSE@example.test"), ("straße@example.test", "strasse@example.test"),
     ("straße@example.test", "strasse@example.test"), False),
    ("imessage", ("Ada@Example.test", "ada@example.test"), ("", ""), ("ada@example.test",) * 2, False),
    ("phone", ("+1 787 555 0100", "+17875550100"), ("", ""), ("+17875550100",) * 2, False),
]


@pytest.mark.parametrize(("platform", "spellings", "spelling_keys", "person_keys", "one"), SPELLINGS)
def test_a_spelling_lands_on_the_stored_handle_exactly_when_their_spelling_keys_agree(
    db: None, platform: str, spellings: tuple[str, ...], spelling_keys: tuple[str, ...],
    person_keys: tuple[str, ...], one: bool,
) -> None:
    """The spelling key decides which handle an address lands on; the person-matching key only groups a person's."""

    del db
    assert tuple(Handle.spelling_key_of(platform, spelling) for spelling in spellings) == spelling_keys
    assert tuple(Handle.normalize_value(platform, spelling) for spelling in spellings) == person_keys
    first, *others = spellings
    with system_context(reason="test spelling decision table"):
        stored = Handle.objects.upsert(platform=platform, value=first)
        asked = [*((platform, spelling) for spelling in spellings), ("telegram", "x")]
        with CaptureQueriesContext(connection) as queries:
            found = Handle.objects.find_identities(asked)
        landed = [Handle.objects.upsert(platform=platform, value=spelling) for spelling in others]
    assert len(queries) == 1 and queries.captured_queries[0]["sql"].count('."platform" = ') == 2
    assert found == ({(platform, spelling): stored for spelling in spellings} if one else {(platform, first): stored})
    assert all((handle.pk == stored.pk) is one for handle in landed)
    assert Handle._base_manager.get(pk=stored.pk).value == first
    assert Handle._base_manager.get(pk=stored.pk).spelling_key == spelling_keys[0]


def test_a_spelling_lands_on_the_exact_row_then_the_live_owner_then_the_oldest_merged_owner(db: None) -> None:
    """The lookup's order; and a row the key repair has not reached answers its own exact spelling."""

    del db
    with system_context(reason="test spelling lookup order"):
        live = Handle.objects.upsert(platform="email", value="Ada@Example.test")
        # A row an earlier rule left unkeyed, of the same address: its own spelling is its own row.
        unkeyed = Handle._base_manager.create(platform="email", value="bo@example.test")
        Handle._base_manager.filter(pk=unkeyed.pk).update(value="ADA@example.test", spelling_key="")
        survivor = Handle.objects.upsert(platform="email", value="cy@work.example")
        merged = Handle.objects.upsert(platform="email", value="Cy@Example.test")
        survivor.fold([merged])
        found = Handle.objects.find_identities(
            [("email", "ADA@example.test"), ("email", "ada@EXAMPLE.test"), ("email", "cy@example.test")],
        )
    assert found == {
        ("email", "ADA@example.test"): unkeyed,
        ("email", "ada@EXAMPLE.test"): live,
        # A spelling of an address merged away lands on its survivor, as the exact spelling does.
        ("email", "cy@example.test"): survivor,
    }


def test_behind_an_external_id_another_spelling_rewrites_nothing_and_a_moved_address_lands_on_its_owner(
    db: None,
) -> None:
    """The account id's row wins unless the reported address is another one that a different row owns."""

    del db
    with system_context(reason="test spelling behind an external id"):
        account = Handle.objects.upsert(platform="email", value="Ada@Example.test", external_id="acct-ada")
        versions = Handle.history.filter(id=account.pk).count()
        reported = Handle.objects.find_identity(platform="email", value="ada@example.test", external_id="acct-ada")
        assert reported == account
        spelled = Handle.objects.upsert(platform="email", value="ada@example.test", external_id="acct-ada")
        assert (spelled.pk, Handle._base_manager.get(pk=account.pk).value) == (account.pk, "Ada@Example.test")
        assert Handle.history.filter(id=account.pk).count() == versions
        # Before the key repair reaches the row, its own value decides (``Handle.is_spelling_of``), in one read.
        Handle._base_manager.filter(pk=account.pk).update(spelling_key="", normalized_version=1)
        with CaptureQueriesContext(connection) as queries:
            Handle.objects.upsert(platform="email", value="ADA@example.test", external_id="acct-ada")
        assert len(queries) == 1
        assert Handle._base_manager.values_list("value", "spelling_key").get(pk=account.pk) == ("Ada@Example.test", "")
        contact = Handle.objects.upsert(platform="email", value="bo@example.test")
        moved = Handle.objects.find_identity(platform="email", value="BO@example.test", external_id="acct-ada")
    assert moved == contact


@pytest.mark.parametrize("signed_in", ["ada@example.com", "Ada@Example.com"], ids=["exact", "another letter case"])
def test_an_account_signing_in_with_a_contacts_address_in_any_letter_case_takes_that_handle(
    db: None, signed_in: str,
) -> None:
    """Its own sign-in address is the account's: control and holding move to its person, on the contact's row."""

    del db
    User = get_user_model()
    with system_context(reason="test sign-in on a contact's address"):
        typist = User.objects.create_user(username="contact-typist")
        contact = Party.objects.create(display_name="Ada (contact)", created_by=typist)
        handle = Handle.objects.upsert(platform="email", value="ada@example.com", created_by_id=typist.pk)
        Party.objects.assert_handle(contact, handle, asserted_by=typist)
        account = User.objects.create_user(username="ada-account", email=signed_in)
        claimed = Handle.objects.claim_control(account, platform="email", value=signed_in, asserted_by=account)
        person = Person.objects.for_user(account)
        claimed.refresh_from_db()
    assert claimed.pk == handle.pk and Handle._base_manager.count() == 1
    assert (claimed.value, claimed.controller_id, claimed.party_id) == ("ada@example.com", account.pk, person.pk)


@pytest.fixture
def owner(db: None) -> Any:
    """Create an ordinary actor after the parties test graph is prepared."""

    del db
    return get_user_model().objects.create_user(username="party-lookup-owner")


@pytest.mark.parametrize("name", ["Alex Example", "alex example", "ALEX EXAMPLE", " \tAlex\n  Example\t "])
def test_named_matches_case_and_whitespace_on_both_sides(owner: Any, name: str) -> None:
    """Stored and supplied whitespace normalize to the same exact name."""

    with system_context(reason="test exact party names"):
        party = Party.objects.create(display_name="\t Alex  \n Example  ", created_by=owner)
        Party.objects.create(display_name="Alex Exampleton", created_by=owner)
        Party.objects.create(display_name="Other Alex Example", created_by=owner)

    candidates = Party.objects.with_actor(owner).named(name)

    assert isinstance(candidates, PartyQuerySet)
    assert list(candidates.values_list("pk", flat=True)) == [party.pk]


def test_named_matches_legal_name_and_keeps_multiple_candidates(owner: Any) -> None:
    """A legal name and a display name can independently identify candidates."""

    with system_context(reason="test legal party names"):
        organization = Organization.objects.create(
            display_name="Northstar",
            legal_name=" Northstar\t  Studio ",
            created_by=owner,
        )
        party = Party.objects.create(display_name="NORTHSTAR STUDIO", created_by=owner)

    candidates = Party.objects.with_actor(owner).named("northstar studio")

    assert set(candidates.values_list("pk", flat=True)) == {organization.pk, party.pk}
    assert list(candidates.filter(pk=organization.pk).values_list("pk", flat=True)) == [organization.pk]
    assert list(Organization.objects.with_actor(owner).named(" NORTHSTAR  STUDIO ")) == [organization]


def test_named_treats_pattern_characters_literally(owner: Any) -> None:
    """Punctuation cannot broaden exact matching into a regular expression."""

    with system_context(reason="test literal party name"):
        party = Party.objects.create(display_name="A. (Studio)+ [One]", created_by=owner)
        Party.objects.create(display_name="Ax Studiooooo One", created_by=owner)

    assert list(Party.objects.with_actor(owner).named("a. (studio)+ [one]")) == [party]


@pytest.mark.parametrize("name", ["", " \t\n "])
def test_named_empty_name_has_no_candidates(owner: Any, name: str) -> None:
    """An absent name never matches empty display or legal names."""

    with system_context(reason="test empty party name"):
        Organization.objects.create(display_name="", legal_name="", created_by=owner)

    assert not Party.objects.with_actor(owner).named(name).exists()


def test_named_excludes_merged_display_and_legal_names(owner: Any) -> None:
    """Neither name of a merged row appears among canonical candidates."""

    with system_context(reason="test merged party name"):
        canonical = Party.objects.create(display_name="Northstar Studio", created_by=owner)
        merged = Organization.objects.create(
            display_name="Northstar Studio",
            legal_name="Northstar Legal",
            merged_into=canonical,
            created_by=owner,
        )

    candidates = Party.objects.with_actor(owner).named("northstar studio")
    assert list(candidates.values_list("pk", flat=True)) == [canonical.pk]
    assert not Party.objects.with_actor(owner).named(merged.legal_name).exists()


def test_named_preserves_filters_and_actor_scope(owner: Any) -> None:
    """Name matching cannot expose another actor's identically named party."""

    other = get_user_model().objects.create_user(username="party-lookup-other")
    with system_context(reason="test scoped party names"):
        visible = Party.objects.create(display_name="Alex Example", created_by=owner)
        hidden = Party.objects.create(display_name="Alex Example", created_by=other)

    assert list(Party.objects.with_actor(owner).named("alex example")) == [visible]
    assert not Party.objects.with_actor(owner).filter(pk=hidden.pk).named("alex example").exists()


def test_named_respects_the_legal_name_owners_read_scope(owner: Any) -> None:
    """Sharing a parent identity does not expose an unreadable child's legal name."""

    other = get_user_model().objects.create_user(username="legal-name-owner")
    with system_context(reason="test scoped legal name"):
        organization = Organization.objects.create(
            display_name="Northstar",
            legal_name="Northstar Studio",
            created_by=other,
        )
        parent = Party.objects.get(pk=organization.pk)
        parent.grant_record_access("reader", owner)

    assert list(Party.objects.with_actor(owner).named("Northstar")) == [parent]
    assert not Party.objects.with_actor(owner).named("Northstar Studio").exists()


@pytest.mark.django_db(transaction=True)
def test_named_excludes_archived_rows_in_an_archivable_composition(owner: Any) -> None:
    """An archive-aware party queryset composes its native archive predicate."""

    with isolate_apps():

        class ArchivablePartyQuerySet(ArchiveQuerySet, PartyQuerySet):
            """A party composition that adds the shared archive lifecycle."""

        class ArchivableParty(ArchiveMixin, Party):
            """Alternate concrete party composition for archive-scope coverage."""

            objects = AngeeManager.from_queryset(ArchivablePartyQuerySet)()

            class Meta:
                """Keep this alternate test table outside installed app setup."""

                app_label = "party_lookup"
                rebac_resource_type = "parties/party"

        with (
            model_tables((ArchivableParty, ArchivableParty.history.model)),
            system_context(reason="test archived party names"),
        ):
            active = ArchivableParty.objects.create(display_name="Alex Example", created_by=owner)
            ArchivableParty.objects.create(display_name="Alex Example", is_archived=True, created_by=owner)
            assert list(ArchivableParty.objects.named("alex example")) == [active]


@pytest.mark.parametrize(
    ("platform", "stored", "query"),
    [
        ("email", "Alex.Example+team@gmail.com", " alexexample@GMAIL.COM "),
        ("phone", "+420 777 123 456", "+420777123456"),
    ],
)
def test_confirmed_uses_platform_normalization(owner: Any, platform: str, stored: str, query: str) -> None:
    """A confirmed party link is found through the handle normalization owner."""

    with system_context(reason="test confirmed handle lookup"):
        party = Party.objects.create(display_name="Alex Example", created_by=owner)
        handle = Handle.objects.create(platform=platform, value=stored, created_by=owner)
        party.with_actor(owner).confirm_statement(aspect="handle", handle=handle, values={})

    candidates = Handle.objects.with_actor(owner).confirmed(platform, query)

    assert list(candidates) == [handle]
    assert candidates.get().party_id == party.pk
    assert not Handle.objects.with_actor(owner).filter(pk=-1).confirmed(platform, query).exists()
    assert not Handle.objects.with_actor(owner).confirmed("other", query).exists()


def test_confirmed_ignores_unconfirmed_and_unlinked_handles(owner: Any) -> None:
    """Verification alone does not establish a confirmed party identity."""

    with system_context(reason="test unconfirmed handle lookup"):
        party = Party.objects.create(display_name="Alex Example", created_by=owner)
        handle = Handle.objects.create(platform="email", value="alex@example.test", is_verified=True, created_by=owner)
        Party.objects.assert_handle(party, handle, asserted_by=owner, confidence=1.0)
        Handle.objects.create(
            platform="email",
            value="unlinked@example.test",
            party_link_confirmed=True,
            created_by=owner,
        )

    assert not Handle.objects.with_actor(owner).confirmed("email", handle.value).exists()
    assert not Handle.objects.with_actor(owner).confirmed("email", "unlinked@example.test").exists()


def test_confirmed_preserves_actor_scope(owner: Any) -> None:
    """A confirmed foreign handle stays hidden from the pinned actor."""

    other = get_user_model().objects.create_user(username="handle-lookup-other")
    with system_context(reason="test scoped confirmed handle"):
        party = Party.objects.create(display_name="Morgan Example", created_by=other)
        handle = Handle.objects.create(platform="email", value="morgan@example.test", created_by=other)
        party.with_actor(other).confirm_statement(aspect="handle", handle=handle, values={})

    assert not Handle.objects.with_actor(owner).confirmed("email", handle.value).exists()


@pytest.mark.parametrize("deferred", [False, True], ids=["loaded", "deferred-parent-fields"])
def test_child_binds_parent_statements_without_a_query(owner: Any, deferred: bool) -> None:
    """An MTI child's parent-declared statements bind the ancestor row without a query.

    A GraphQL read defers parent columns it does not select; the ancestor still
    binds by the child's primary key, so provenance reads cost only their own query.
    """

    with system_context(reason="test parent statements of a child"):
        organization = Organization.objects.create(
            display_name="Upcast Studio", legal_name="Upcast Legal", created_by=owner
        )
        organization.with_actor(owner).confirm_statement(aspect="identity", values={"display_name": "Upcast Studio"})
    rows = Organization.objects.with_actor(owner)
    reader = (rows.only("pk", "legal_name") if deferred else rows).get(pk=organization.pk)

    with CaptureQueriesContext(connection) as captured:
        subjects = [manager.instance for manager in reader.statement_managers()]
    assert captured.captured_queries == []
    assert type(subjects[0]) is Party
    assert all(subject.pk == organization.pk for subject in subjects)
    with system_context(reason="test parent provenance of a child"):
        assert list(reader.provenance("display_name").values_list("display_name", flat=True)) == ["Upcast Studio"]
