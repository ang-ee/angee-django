"""Credential exchange boundaries and reconnect fences at their native row owner."""

from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone
from rebac import system_context

from angee.base.locks import record_lock_key, task_lock
from angee.integrate.errors import IntegrationError
from angee.integrate.oauth.client import OAuthClientProtocol
from tests.conftest import Credential, make_integration

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("composed_tables")]


@pytest.mark.parametrize("refused", [False, True])
def test_refresh_does_not_overwrite_a_reconnected_grant(monkeypatch, refused):
    with system_context(reason="test refresh reconnect fence"):
        credential = make_integration(
            "refresh-fence", kind="oauth", material={"access_token": "old", "refresh_token": "old-refresh"},
        ).credential
        credential.expires_at = timezone.now() - timedelta(minutes=1)
        credential.save(update_fields=["expires_at"])

    def exchange(protocol, *, refresh_token):
        assert not connection.in_atomic_block
        assert refresh_token == "old-refresh"
        Credential.objects.upsert_for_user(
            credential.user, credential.oauth_client, "oauth",
            {"access_token": "reconnected", "refresh_token": "new-refresh"},
        )
        if refused:
            raise IntegrationError("The superseded grant was refused.")
        return {"access_token": "stale-result", "refresh_token": "stale-rotation"}

    monkeypatch.setattr(OAuthClientProtocol, "refresh_token", exchange)
    with system_context(reason="test refresh fence application"):
        if refused:
            with pytest.raises(IntegrationError):
                credential.refresh_now()
        else:
            credential.refresh_now()
        credential.refresh_from_db()
        assert credential.reveal() == {"access_token": "reconnected", "refresh_token": "new-refresh"}
        assert credential.last_refresh_status == "ok"


def test_refresh_rechecks_freshness_after_another_consumer_won(monkeypatch):
    with system_context(reason="test refresh freshness reread"):
        credential = make_integration(
            "refresh-reread", kind="oauth", material={"access_token": "old", "refresh_token": "old-refresh"},
        ).credential
        credential.expires_at = timezone.now() - timedelta(minutes=1)
        credential.save(update_fields=["expires_at"])
        stale = Credential.objects.get(pk=credential.pk)
        Credential.objects.upsert_for_user(
            credential.user, credential.oauth_client, "oauth",
            {"access_token": "winner", "refresh_token": "rotation", "expires_in": 3600}, reconnect=False,
        )

        def no_exchange(*args, **kwargs):
            raise AssertionError("A stale caller must not replay the rotated grant.")

        monkeypatch.setattr(OAuthClientProtocol, "refresh_token", no_exchange)
        stale.ensure_fresh()
        assert stale.reveal()["access_token"] == "winner"


def test_refresh_serialization_refuses_a_second_rotating_exchange(monkeypatch):
    with system_context(reason="test rotating refresh exclusion"):
        credential = make_integration(
            "refresh-exclusion", kind="oauth", material={"access_token": "old", "refresh_token": "one-time"},
        ).credential

        def no_exchange(*args, **kwargs):
            raise AssertionError("The competing refresh must not exchange a rotating token.")

        monkeypatch.setattr(OAuthClientProtocol, "refresh_token", no_exchange)
        key = record_lock_key(credential._meta.label_lower, credential.pk, "refresh")
        with task_lock(key) as acquired:
            assert acquired
            with pytest.raises(IntegrationError) as error:
                credential.refresh_now()
            assert error.value.transient


def test_refresh_retains_a_rotated_token_when_only_the_label_changed(monkeypatch):
    with system_context(reason="test refresh unrelated label edit"):
        credential = make_integration(
            "refresh-label", kind="oauth", material={"access_token": "old", "refresh_token": "one-time"},
        ).credential

    def exchange(protocol, *, refresh_token):
        current = Credential.objects.get(pk=credential.pk)
        current.name = "Renamed during provider exchange"
        current.save(update_fields=["name", "updated_at"])
        return {"access_token": "fresh", "refresh_token": "rotated"}

    monkeypatch.setattr(OAuthClientProtocol, "refresh_token", exchange)
    with system_context(reason="test rotated token survives unrelated edit"):
        credential.refresh_now()
        assert credential.reveal() == {"access_token": "fresh", "refresh_token": "rotated"}
        assert credential.name == "Renamed during provider exchange"
