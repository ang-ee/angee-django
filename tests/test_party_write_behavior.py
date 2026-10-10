"""Party write behavior."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import override_settings
from rebac import actor_context, system_context

import tests.test_parties_circles  # noqa: F401 -- register the fixture model graph before database setup
from angee.messaging import managers as messaging_managers
from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.messaging.testing.models import Address, Channel, Fragment, Handle, Message, Part, Party, PartyHandle
from angee.parties.managers import Signing
from angee.parties.mixins import LinkSource
from angee.parties.tasks import refresh_handle_suggestions
from tests.conftest import make_integration


@pytest.mark.django_db(transaction=True)
def test_handle_upsert_collision_refreshes_existing_row(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Handle upsert collision refreshes existing row."""
    del composed_tables
    with system_context(reason="handle refresh fixture"):
        source = Handle.objects.upsert(platform="email", value="old@example.test", external_id="stable-account")
        existing = Handle.objects.upsert(platform="email", value="new@example.test", display_name="Before")
        owner = Handle.objects
        refreshed = owner.upsert(
            platform="email", value="new@example.test", external_id="stable-account", display_name="After"
        )
        existing.refresh_from_db()
        source.refresh_from_db()
    assert refreshed.pk == existing.pk
    assert existing.display_name == "After"
    assert source.value == "old@example.test"


@pytest.mark.django_db(transaction=True)
def test_display_name_suggestions_need_a_personal_full_name_the_party_bears(composed_tables: None) -> None:
    """Only a full name the party itself bears is suggested, once and for review only."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="routed-suggestion-owner")
    with system_context(reason="suggestion fixture"):

        def handle(platform: str, value: str, name: str, party: Party | None = None) -> Handle:
            row = Handle._base_manager.create(platform=platform, value=value, display_name=name, created_by=owner)
            if party is not None:
                PartyHandle.objects.link(party, row, source=LinkSource.CARDDAV, created_by_id=owner.pk)
            return row

        ada = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        brand = Party._base_manager.create(display_name="Customer", created_by=owner)
        grab_bag = Party._base_manager.create(display_name="Unknown", created_by=owner)
        handle("email", "ada@example.test", "Ada Lovelace", ada)
        handle("email", "customer@example.test", "Customer", brand)
        handle("phone", "+420777000001", "Grace Hopper", grab_bag)
        candidate = handle("phone", "+420777123456", "Ada Lovelace")
        handle("phone", "+420777123457", "Customer")
        handle("email", "grace@example.test", "Grace Hopper")
        notifications = handle("email", "notifications@example.test", "Ada Lovelace")

        shared = frozenset({notifications.pk})
        created = PartyHandle.objects._reconcile_display_names(shared)
        repeated = PartyHandle.objects._reconcile_display_names(shared)
        suggested = set(PartyHandle._base_manager.filter(source=LinkSource.RULE).values_list("party_id", "handle_id"))
        candidate.refresh_from_db()
        ada.refresh_from_db()
        # The handle gains an owner: the names still support the guess, so it stays.
        PartyHandle.objects.link(grab_bag, candidate, source=LinkSource.CARDDAV, created_by_id=owner.pk)
        kept = PartyHandle.objects._reconcile_display_names(shared)
        # The name changes: the pass withdraws the guess it no longer supports.
        Handle._base_manager.filter(pk=candidate.pk).update(display_name="Someone Else")
        withdrawn = PartyHandle.objects._reconcile_display_names(shared)
    assert (created, repeated, kept, withdrawn) == (1, 0, 0, 1)
    assert suggested == {(ada.pk, candidate.pk)}
    assert candidate.party_id is None
    assert ada.handle_count == 1
    assert not PartyHandle._base_manager.filter(source=LinkSource.RULE).exists()


@pytest.mark.django_db(transaction=True)
def test_stale_owner_repair_unowns_a_handle_a_suggestion_decided(composed_tables: None) -> None:
    """A handle a suggestion owned under the old resolution rule is re-resolved once."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="stale-owner-repair-owner")
    with system_context(reason="stale owner fixture"):
        party = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        handle = Handle._base_manager.create(platform="phone", value="+14155552671", created_by=owner)
        PartyHandle.objects.link(party, handle, confidence=0.3, source=LinkSource.RULE, created_by_id=owner.pk)
        Handle._base_manager.filter(pk=handle.pk).update(party=party)
        Party._base_manager.filter(pk=party.pk).update(handle_count=1)

        repaired = PartyHandle.objects.resolve_stale_owners()
        repeated = PartyHandle.objects.resolve_stale_owners()
        handle.refresh_from_db()
        party.refresh_from_db()
    assert (repaired, repeated) == (1, 0)
    assert handle.party_id is None
    assert party.handle_count == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("batch", [1, 2000])
def test_signature_phones_are_mined_only_from_a_senders_own_signature(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch, batch: int
) -> None:
    """Only a number one sender alone signs with is mined, and only a real phone number.

    Fragment texts load in batches; a batch of one crosses a boundary at every row.
    """
    del composed_tables
    monkeypatch.setattr(messaging_managers, "_SIGNING_TEXT_BATCH", batch)
    owner = get_user_model().objects.create_user(username="signature-mining-owner")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="signature mining fixture"):
        ada = Party._base_manager.create(display_name="Ada", created_by=owner)
        senders = {
            name: Handle._base_manager.create(platform="email", value=f"{name}@example.test", created_by=owner)
            for name in ("ada", "carol")
        }
        PartyHandle.objects.link(ada, senders["ada"], source=LinkSource.CARDDAV, created_by_id=owner.pk)
        own = Fragment.objects.upsert(
            text="Ada Lovelace\n+1 415 555 2671\nIssue 1721429715\nAnalytical Engines +1 787 906 0900",
            created_by_id=owner.pk,
        )
        shared = Fragment.objects.upsert(text="Monica Alvarez\n+1 787 523 6508", created_by_id=owner.pk)
        switchboard = Fragment.objects.upsert(text="Carol\nAnalytical Engines +1 787 906 0900", created_by_id=owner.pk)
        for sender, fragment in (("ada", own), ("ada", shared), ("carol", shared), ("carol", switchboard)):
            message = Message._base_manager.create(
                channel=channel,
                created_by=owner,
                sender=senders[sender],
                direction="inbound",
                status="synced",
                sent_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            Part._base_manager.create(created_by=owner, message=message, role="signature", fragment=fragment)
        refresh_handle_suggestions()
        mined = set(
            PartyHandle._base_manager.filter(source=LinkSource.RULE, handle__platform="phone").values_list(
                "party_id", "handle__normalized_value"
            )
        )
    assert mined == {(ada.pk, "+14155552671")}
    # The verb reads every owner's mail whoever calls it: a stranger's run withdraws nothing.
    stranger = get_user_model().objects.create_user(username="signature-mining-stranger")
    with actor_context(stranger):
        assert PartyHandle.objects.reconcile_suggestions() == 0
    with system_context(reason="signature mining read"):
        assert PartyHandle._base_manager.filter(source=LinkSource.RULE, handle__platform="phone").count() == 1


@pytest.mark.django_db(transaction=True)
def test_a_signature_pass_withdraws_only_undecided_suggestions_it_no_longer_supports(composed_tables: None) -> None:
    """Once the evidence is gone the guess goes; the confirmed link still owns and the dismissal stays."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="signature-withdrawal-owner")
    with system_context(reason="signature withdrawal fixture"):
        party = Party._base_manager.create(display_name="Ada", created_by=owner)
        PartyHandle.objects._reconcile_signatures(
            [Signing("ada-signature", "+1 415 555 2671\n+1 415 555 2672\n+1 415 555 2673", "ada", party.pk, owner.pk)]
        )
        confirmed, dismissed, unreviewed = PartyHandle._base_manager.filter(party=party).order_by(
            "handle__normalized_value"
        )
        with actor_context(owner):
            confirmed.confirm()
            dismissed.dismiss()
        withdrawn = PartyHandle.objects._reconcile_signatures([])
        remaining = set(PartyHandle._base_manager.filter(party=party).values_list("pk", flat=True))
        handle = Handle._base_manager.get(pk=unreviewed.handle_id)
        party.refresh_from_db()
    assert withdrawn == 1
    assert remaining == {confirmed.pk, dismissed.pk}
    assert handle.party_id is None
    assert party.handle_count == 1


@pytest.mark.django_db(transaction=True)
def test_a_number_is_one_persons_until_another_person_signs_with_it(composed_tables: None) -> None:
    """Signers count per person: Ada's two addresses are one signer, and Carol makes the number shared."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="signature-person-owner")
    with system_context(reason="signature person fixture"):
        ada = Party._base_manager.create(display_name="Ada", created_by=owner)
        carol = Party._base_manager.create(display_name="Carol", created_by=owner)
        mobile = "Ada Lovelace\n+1 415 555 2671"
        from_two_addresses = [
            Signing("ada-work", mobile, "ada-work", ada.pk, owner.pk),
            Signing("ada-home", mobile, "ada-home", ada.pk, owner.pk),
        ]
        assert PartyHandle.objects._reconcile_signatures(from_two_addresses) == 1
        shared = [*from_two_addresses, Signing("carol", "Carol\n+1 415 555 2671", "carol", carol.pk, owner.pk)]
        assert PartyHandle.objects._reconcile_signatures(shared) == 1
        assert not PartyHandle._base_manager.filter(source=LinkSource.RULE).exists()


@pytest.mark.django_db(transaction=True)
def test_another_owners_mail_neither_proposes_nor_keeps_a_suggestion(composed_tables: None) -> None:
    """Support stays inside the owner partition: Bob's private mail never decides Alice's review queue."""
    del composed_tables
    alice, bob = (
        get_user_model().objects.create_user(username=f"signature-partition-{name}") for name in ("alice", "bob")
    )
    with system_context(reason="signature partition fixture"):
        ada = Party._base_manager.create(display_name="Ada", created_by=alice)
        carol = Party._base_manager.create(display_name="Carol", created_by=alice)
        text = "Analytical Engines\n+1 787 906 0900"
        alice_ada = Signing("engines", text, "ada", ada.pk, alice.pk)
        alice_carol = Signing("engines", text, "carol", carol.pk, alice.pk)
        bob_ada = Signing("engines", text, "ada-at-bob", ada.pk, bob.pk)
        assert PartyHandle.objects._reconcile_signatures([alice_ada, alice_carol, bob_ada]) == 0
        assert PartyHandle.objects._reconcile_signatures([alice_ada]) == 1
        # Carol signs with it in Alice's mail: Bob's mail cannot keep Alice's suggestion.
        assert PartyHandle.objects._reconcile_signatures([alice_ada, alice_carol, bob_ada]) == 1
        assert not PartyHandle._base_manager.filter(source=LinkSource.RULE).exists()


@pytest.mark.django_db(transaction=True)
def test_a_list_senders_display_name_proposes_nobody(composed_tables: None) -> None:
    """Messaging's shared-sender provider keeps a list address's display name out of the evidence."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="shared-sender-owner")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="shared sender fixture"):
        ada = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        own = Handle._base_manager.create(
            platform="phone", value="+14155552671", display_name="Ada Lovelace", created_by=owner
        )
        PartyHandle.objects.link(ada, own, source=LinkSource.CARDDAV, created_by_id=owner.pk)
        for external_id, value, headers in (
            ("list-1", "list@example.test", (("list-id", "<repo.example.test>"),)),
            ("person-1", "ada@example.test", ()),
        ):
            parsed = ParsedMessage(
                external_id=external_id,
                platform="email",
                subject="Hello",
                sender=ParsedHandle(platform="email", value=value, display_name="Ada Lovelace"),
                body=ParsedPart(text=external_id),
                headers=headers,
            )
            Message.objects.ingest([parsed], channel=channel, quote_edges=False)
    assert PartyHandle.objects.reconcile_suggestions() == 1
    with system_context(reason="shared sender read"):
        suggested = PartyHandle._base_manager.filter(source=LinkSource.RULE).values_list("handle__value", flat=True)
        assert set(suggested) == {"ada@example.test"}


@pytest.mark.django_db(transaction=True)
def test_an_assertion_never_reopens_a_dismissed_link(composed_tables: None) -> None:
    """A card that lists a dismissed number leaves the human decision as it was."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="dismissed-link-owner")
    with system_context(reason="dismissed link fixture"):
        party = Party._base_manager.create(display_name="Ada", created_by=owner)
        handle = Handle._base_manager.create(platform="phone", value="+14155552671", created_by=owner)
        link = PartyHandle.objects.link(party, handle, confidence=0.3, source=LinkSource.RULE, created_by_id=owner.pk)
        with actor_context(owner):
            link.dismiss()
        PartyHandle.objects.link(party, handle, source=LinkSource.CARDDAV, created_by_id=owner.pk)
        link.refresh_from_db()
        handle.refresh_from_db()
    assert (link.is_dismissed, link.source, link.confidence) == (True, LinkSource.RULE, 0.3)
    assert handle.party_id is None


@pytest.mark.django_db(transaction=True)
def test_without_a_signing_provider_signature_suggestions_stay(composed_tables: None) -> None:
    """No registered provider is no evidence to judge, not evidence that every number is gone."""
    del composed_tables
    owner = get_user_model().objects.create_user(username="signature-provider-owner")
    with system_context(reason="signature provider fixture"):
        party = Party._base_manager.create(display_name="Ada", created_by=owner)
        PartyHandle.objects._reconcile_signatures(
            [Signing("ada-signature", "Ada Lovelace\n+1 415 555 2671", "ada", party.pk, owner.pk)]
        )
        with override_settings(ANGEE_PARTIES_SIGNING_PROVIDERS=[]):
            assert PartyHandle.objects.reconcile_suggestions() == 0
        assert PartyHandle._base_manager.filter(party=party, source=LinkSource.RULE).count() == 1


@pytest.mark.django_db(transaction=True)
def test_primary_address_save_demotes_previous_primary(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Primary address save demotes previous primary."""
    del composed_tables
    with system_context(reason="primary address fixture"):
        party = Party._base_manager.create(display_name="Customer")
        previous = Address._base_manager.create(party=party, is_primary=True)
        selected = Address._base_manager.create(party=party)
        selected.is_primary = True
        selected.save(update_fields=["is_primary"])
        previous.refresh_from_db()
        selected.refresh_from_db()
    assert selected.is_primary is True
    assert previous.is_primary is False


@pytest.mark.django_db(transaction=True)
def test_party_link_delete_repairs_after_commit(composed_tables: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Party link delete repairs after commit."""
    del composed_tables
    with system_context(reason="delete repair fixture"):
        party = Party._base_manager.create(display_name="Customer")
        handle = Handle.objects.upsert(platform="email", value="repair@example.test")
        link = PartyHandle.objects.link(party, handle, source=LinkSource.MANUAL)
        with transaction.atomic():
            link.delete()
            assert Handle._base_manager.get(pk=handle.pk).party_id == party.pk
        handle.refresh_from_db()
        party.refresh_from_db()
    assert handle.party_id is None
    assert party.handle_count == 0
