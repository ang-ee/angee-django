"""Credential refresh: the exchange runs outside transactions and persists under the grant's fence."""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

import pytest
from django.db import connection, transaction
from django.utils import timezone
from rebac import system_context

from angee.base.locks import record_lock_key, task_lock
from angee.integrate.credentials import CredentialKind
from angee.integrate.errors import IntegrationError
from angee.integrate.oauth.client import OAuthClientProtocol
from tests.conftest import Credential, create_user, make_integration

pytestmark = pytest.mark.django_db(transaction=True)


def _expired_credential(slug: str, material: dict[str, Any]) -> Any:
    with system_context(reason="test credential refresh setup"):
        credential = make_integration(slug, kind="oauth", material=material).credential
        credential.expires_at = timezone.now() - timedelta(minutes=1)
        credential.save(update_fields=["expires_at"])
    return credential


@pytest.mark.parametrize("refused", [False, True])
def test_refresh_does_not_overwrite_a_reconnected_grant(monkeypatch: pytest.MonkeyPatch, refused: bool) -> None:
    """A reconnect during the provider exchange wins over its result or its refusal."""

    credential = _expired_credential("refresh-fence", {"access_token": "old", "refresh_token": "old-refresh"})

    def exchange(protocol: Any, *, refresh_token: str) -> dict[str, Any]:
        assert not connection.in_atomic_block
        assert refresh_token == "old-refresh"
        Credential.objects.upsert_for_user(
            credential.user,
            credential.oauth_client,
            "oauth",
            {"access_token": "reconnected", "refresh_token": "new-refresh"},
        )
        if refused:
            raise IntegrationError("The superseded grant was refused.")
        return {"access_token": "stale-result", "refresh_token": "stale-rotation"}

    monkeypatch.setattr(OAuthClientProtocol, "refresh_token", exchange)
    with system_context(reason="test refresh fence"):
        if refused:
            with pytest.raises(IntegrationError):
                credential.refresh_now()
        else:
            credential.refresh_now()
        credential.refresh_from_db()
    assert credential.reveal() == {"access_token": "reconnected", "refresh_token": "new-refresh"}
    assert credential.last_refresh_status == "ok"


def test_refresh_rechecks_freshness_after_another_consumer_won(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale caller adopts the persisted token instead of replaying the rotated grant."""

    credential = _expired_credential("refresh-reread", {"access_token": "old", "refresh_token": "old-refresh"})
    with system_context(reason="test refresh freshness reread"):
        stale = Credential.objects.get(pk=credential.pk)
        Credential.objects.upsert_for_user(
            credential.user,
            credential.oauth_client,
            "oauth",
            {"access_token": "winner", "refresh_token": "rotation", "expires_in": 3600},
            reconnect=False,
        )

    def no_exchange(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("A stale caller must not replay the rotated grant.")

    monkeypatch.setattr(OAuthClientProtocol, "refresh_token", no_exchange)
    with system_context(reason="test refresh freshness adopt"):
        stale.ensure_fresh()
    assert stale.reveal()["access_token"] == "winner"


def test_refresh_waits_for_the_holder_and_adopts_its_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second consumer blocks on the refresh lock, then adopts what the holder persisted."""

    credential = _expired_credential("refresh-waits", {"access_token": "old", "refresh_token": "one-time"})
    with system_context(reason="test refresh contention setup"):
        stale = Credential.objects.get(pk=credential.pk)
    key = record_lock_key(credential._meta.label_lower, credential.pk, "refresh")
    events: list[str] = []
    holding, persisted = threading.Event(), threading.Event()

    def hold() -> None:
        with task_lock(key) as acquired:
            assert acquired
            events.append("held")
            holding.set()
            assert persisted.wait(timeout=10)
            events.append("released")

    def no_exchange(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("The waiting consumer must adopt the holder's token.")

    monkeypatch.setattr(OAuthClientProtocol, "refresh_token", no_exchange)
    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert holding.wait(timeout=10)
        with system_context(reason="test refresh contention winner"):
            Credential.objects.upsert_for_user(
                credential.user,
                credential.oauth_client,
                "oauth",
                {"access_token": "winner", "refresh_token": "rotated", "expires_in": 3600},
                reconnect=False,
            )
        threading.Timer(0.2, persisted.set).start()
        with system_context(reason="test refresh contention waiter"):
            stale.ensure_fresh()
        events.append("adopted")
    finally:
        persisted.set()
        holder.join(timeout=10)
    assert events == ["held", "released", "adopted"]
    assert stale.reveal() == {"access_token": "winner", "refresh_token": "rotated", "expires_in": 3600}


def test_refresh_retains_a_rotated_token_when_only_the_label_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrelated edit during the exchange does not discard a one-time rotated token."""

    credential = _expired_credential("refresh-label", {"access_token": "old", "refresh_token": "one-time"})

    def exchange(protocol: Any, *, refresh_token: str) -> dict[str, Any]:
        current = Credential.objects.get(pk=credential.pk)
        current.name = "Renamed during provider exchange"
        current.save(update_fields=["name", "updated_at"])
        return {"access_token": "fresh", "refresh_token": "rotated"}

    monkeypatch.setattr(OAuthClientProtocol, "refresh_token", exchange)
    with system_context(reason="test rotated token survives an unrelated edit"):
        credential.refresh_now()
    assert credential.reveal() == {"access_token": "fresh", "refresh_token": "rotated"}
    assert credential.name == "Renamed during provider exchange"


def test_a_refresh_that_does_not_rotate_keeps_the_stored_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The OAuth kind carries the stored refresh token forward over a response that omits it."""

    credential = _expired_credential("refresh-kept", {"access_token": "old", "refresh_token": "long-lived"})
    monkeypatch.setattr(
        OAuthClientProtocol, "refresh_token", lambda protocol, *, refresh_token: {"access_token": "new"},
    )
    with system_context(reason="test refresh keeps the refresh token"):
        credential.refresh_now()
    assert credential.reveal() == {"access_token": "new", "refresh_token": "long-lived"}


def test_only_the_oauth_kind_carries_stored_material_forward() -> None:
    """A rotated refresh token wins; another kind's new material replaces the stored one whole."""

    oauth = CredentialKind.OAUTH.handler
    assert oauth.carry_forward({"access_token": "a", "refresh_token": "new"}, {"refresh_token": "old"}) == {
        "access_token": "a", "refresh_token": "new",
    }
    assert CredentialKind.STATIC_TOKEN.handler.carry_forward({"api_key": "new"}, {"api_key": "old", "x": 1}) == {
        "api_key": "new",
    }


def test_refresh_refuses_to_run_inside_a_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider exchange is network I/O, so an enclosing transaction is refused."""

    credential = _expired_credential("refresh-atomic", {"access_token": "old", "refresh_token": "old-refresh"})

    def no_exchange(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("A refresh inside a transaction must not reach the provider.")

    monkeypatch.setattr(OAuthClientProtocol, "refresh_token", no_exchange)
    with system_context(reason="test refresh inside atomic"), transaction.atomic():
        with pytest.raises(RuntimeError, match="requires autocommit"):
            credential.refresh_now()


def _static_token(owner: Any, path: str, token: str) -> Any:
    """Store ``token`` on a new static-token credential, or re-enter it over a valid one."""

    if path == "create":
        return Credential.objects.create_local_credential(
            owner, kind="static_token", name="Pasted", material={"api_key": token},
        )
    credential = Credential.objects.create_local_credential(
        owner, kind="static_token", name="Pasted", material={"api_key": "valid-token"},
    )
    credential.replace_material({"api_key": token})
    return credential


@pytest.mark.parametrize("path", ["create", "re-enter"])
@pytest.mark.parametrize(
    ("typed", "stored"), [("  tok-en_1.~/+= \n", "tok-en_1.~/+="), ("tok-en_1.~/+=", "tok-en_1.~/+=")],
)
def test_a_static_token_is_stored_without_surrounding_whitespace(path: str, typed: str, stored: str) -> None:
    owner = create_user(f"static-token-{path}-{len(typed)}")
    with system_context(reason="test a pasted static token"):
        credential = _static_token(owner, path, typed)
        credential.refresh_from_db()
    assert credential.reveal() == {"api_key": stored}
    assert credential.auth_headers() == {"Authorization": f"Bearer {stored}"}


@pytest.mark.parametrize("path", ["create", "re-enter"])
@pytest.mark.parametrize(
    "secret", ["https://feeds.example.test/my team.ics", "https://feeds.example.test/kalender/\u00f6ffentlich.ics"],
)
def test_a_secret_that_is_never_a_header_is_kept_as_entered(path: str, secret: str) -> None:
    """A static token is not always a header: a calendar feed subscription keeps its feed address as one.

    This pins the shape ``calendar_integrate_ics.connect.subscribe_calendar`` stores (``{"api_key": <address>}``)
    against the handler; the ICS test module is outside this lane.
    """
    owner = create_user(f"kept-secret-{path}-{len(secret)}")
    with system_context(reason="test a secret kept as a static token"):
        credential = _static_token(owner, path, f"  {secret}\n")
        credential.refresh_from_db()
    assert credential.reveal() == {"api_key": secret}
    # Testing the connection proves the secret without building a header from it.
    credential.handler.probe(credential)


@pytest.mark.parametrize(
    "secret", ["https://feeds.example.test/my team.ics", "https://feeds.example.test/kalender/\u00f6ffentlich.ics"],
)
def test_a_connection_whose_static_token_is_never_a_header_tests_usable(secret: str) -> None:
    with system_context(reason="test a feed address kept as a static token"):
        integration = make_integration(f"feed-address-{len(secret)}")
        integration.credential.replace_material({"api_key": secret})
        integration.refresh_from_db()
        assert integration.probe_credential() == "Credential is usable."


@pytest.mark.parametrize("token", ["tok en", "tok\u200ben", "tok\x00en", "t\u00f6ken"])
def test_a_static_token_a_header_cannot_carry_is_refused_when_the_header_is_built(token: str) -> None:
    owner = create_user(f"unsendable-token-{ord(token[3])}")
    with system_context(reason="test a stored token no header can carry"):
        credential = _static_token(owner, "create", token)
        credential.refresh_from_db()
    assert credential.reveal() == {"api_key": token}
    with pytest.raises(IntegrationError) as refused:
        credential.auth_headers()
    error = refused.value
    assert not error.transient
    assert error.public_message == (
        "The stored token contains a character that cannot be sent in a request header; re-enter the token."
    )
    assert token not in str(error) and token not in repr(error)
    assert error.__cause__ is None and error.__context__ is None


@pytest.mark.parametrize("status", ["revoked", "expired"])
def test_an_inactive_static_token_produces_no_headers(status: str) -> None:
    with system_context(reason="test an inactive static token"):
        credential = make_integration(f"inactive-static-{status}").credential
        assert credential.auth_headers() == {"Authorization": "Bearer x"}
        credential.status = status
        credential.save(update_fields=["status", "updated_at"])
        credential.refresh_from_db()
    with pytest.raises(IntegrationError) as refused:
        credential.auth_headers()
    # A sync reports the operator's next step, not an unexpected failure.
    assert (refused.value.public_message, refused.value.transient) == (
        "This credential is no longer active; re-enter the token to use it again.", False,
    )
