"""Evidence-backed handle suggestions follow their evidence; a person's dismissal outlasts both."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from rebac import system_context, to_subject_ref

from angee.messaging.backends import ParsedHandle, ParsedMessage, ParsedPart
from angee.messaging.testing.models import Channel, Handle, Message, Part, Party
from tests.conftest import create_user, make_integration

Evidence = tuple[Any, Any, Callable[[bool], None]]
"""The owner, the suggested party, and a switch that removes or restores the supporting evidence."""


def _display_names() -> Evidence:
    """An unresolved phone shares its full display name with a handle of the party bearing that name."""

    owner = create_user("suggestion-owner")
    with system_context(reason="test display-name evidence"):
        ada = Party._base_manager.create(display_name="Ada Lovelace", created_by=owner)
        email, phone = (
            Handle._base_manager.create(platform=platform, value=value, display_name="Ada Lovelace", created_by=owner)
            for platform, value in (("email", "ada@example.com"), ("phone", "+442079460958"))
        )
        Party.objects.assert_handle(ada, email, asserted_by=owner)

    def name(supported: bool) -> None:
        with system_context(reason="test display-name evidence"):
            phone.display_name = "Ada Lovelace" if supported else "Switchboard"
            phone.save(update_fields=("display_name",))

    return owner, ada, name


def _signatures() -> Evidence:
    """A resolved sender signs with a number nobody else in the owner's mail signs with."""

    channel = make_integration("suggestion-mail", model=Channel, backend_class="manual")
    with system_context(reason="test signature evidence"):
        ada = Party._base_manager.create(display_name="Ada", created_by=channel.owner)
        bob = Party._base_manager.create(display_name="Bob", created_by=channel.owner)

    def signed(party: Any, address: str) -> None:
        parsed = ParsedMessage(
            external_id=address,
            platform="email",
            subject="Hello",
            sender=ParsedHandle(platform="email", value=address),
            body=ParsedPart(role="signature", text=f"{party.display_name}\n+44 20 7946 0958"),
        )
        with system_context(reason="test signature evidence"):
            [message] = Message.objects.ingest([parsed], channel=channel)
            Party.objects.assert_handle(party, message.sender, asserted_by=channel.owner)

    def bob_signs(supported: bool) -> None:
        # A second signer makes the number a switchboard's, nobody's own.
        if not supported:
            signed(bob, "bob@example.com")
            return
        with system_context(reason="test signature evidence"):
            Part._base_manager.filter(message__sender__value="bob@example.com").update(role="body")

    signed(ada, "ada@example.com")
    return channel.owner, ada, bob_signs


@pytest.mark.parametrize(("kind", "evidence"), [("display_name", _display_names), ("signature_phone", _signatures)])
def test_a_suggestion_follows_its_evidence_and_a_dismissal_outlasts_it(
    db: None, kind: str, evidence: Callable[[], Evidence]
) -> None:
    owner, party, supported = evidence()
    statements = Party.statements.model._base_manager
    rule = str(to_subject_ref(Party.objects.rule_source(kind)))

    assert Party.objects.reconcile_suggestions() == 1
    suggestion = statements.get(subject=party, asserted_by=rule)
    assert (suggestion.status, suggestion.applied) == ("asserted", False)
    assert Party.objects.reconcile_suggestions() == 0

    dismissal = suggestion.with_actor(owner).dismiss()
    assert Party.objects.reconcile_suggestions() == 0

    supported(False)
    assert Party.objects.reconcile_suggestions() == 1
    suggestion.refresh_from_db()
    assert suggestion.status == "withdrawn"

    supported(True)
    assert Party.objects.reconcile_suggestions() == 0
    suggestion.refresh_from_db()
    dismissal.refresh_from_db()
    assert suggestion.status == "withdrawn"
    assert (dismissal.is_dismissed, dismissal.status, dismissal.applied) == (True, "asserted", True)
