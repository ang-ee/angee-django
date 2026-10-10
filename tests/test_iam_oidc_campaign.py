"""OIDC must fail closed on ambiguous account keys and retain native linkage."""

import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

from angee.iam_integrate_oidc import identity
from angee.iam_integrate_oidc.errors import IDENTITY_RESOLUTION_FAILED, IdentityFlowError
from angee.iam_integrate_oidc.protocol import OAuthClientOidcProtocol
from angee.integrate.oauth import state
from tests.conftest import ExternalAccount
from tests.iam_campaign import Person
from tests.iam_campaign import legacy_person_emails as legacy_person_emails
from tests.test_oidc import _oauth_client

User = get_user_model()


@pytest.mark.parametrize("link_on_email_match", [False, True])
@pytest.mark.parametrize("create_on_login", [False, True])
def test_oidc_ambiguous_email_never_links_or_provisions(legacy_person_emails, link_on_email_match, create_on_login):
    for username in ("ambiguous-first", "ambiguous-second"):
        User.objects.create_user(username, "ambiguous@example.com")
    oauth_client = _oauth_client(
        link_on_email_match=link_on_email_match,
        create_on_login=create_on_login,
        allowed_email_domains=["example.com"],
    )
    before = User._base_manager.count(), ExternalAccount._base_manager.count(), Person._base_manager.count()
    with pytest.raises(IdentityFlowError) as caught:
        identity.resolve(
            oauth_client,
            sub="unlinked-subject",
            email=" AMBIGUOUS@EXAMPLE.COM ",
            claims={"sub": "unlinked-subject", "email": "ambiguous@example.com", "email_verified": True},
        )
    assert caught.value.code == IDENTITY_RESOLUTION_FAILED
    assert caught.value.http_status == 403
    assert "ambiguous@example.com" not in str(caught.value)
    assert (User._base_manager.count(), ExternalAccount._base_manager.count(), Person._base_manager.count()) == before


@pytest.mark.parametrize("verified", [True, False, None])
def test_oidc_login_establishes_person_only_for_explicitly_verified_email(composed_tables, monkeypatch, verified):
    oauth_client = _oauth_client(create_on_login=True, allowed_email_domains=["example.com"])
    if verified is not True:
        # Unverified emails cannot provision an identity. An already-linked
        # subject may log in, but must not establish a parties claim.
        user = User.objects.create_user("previously-linked", "first.login@example.com")
        with system_context(reason="test.oidc.existing-link"):
            ExternalAccount.objects.link(oauth_client, "first-login", owner=user)
    redirect_uri = "https://app.example/callback"
    state_token, _record = state.issue(oauth_client, redirect_uri)
    claims = {"sub": "first-login", "email": "First.Login@EXAMPLE.COM"}
    if verified is not None:
        claims["email_verified"] = verified
    monkeypatch.setattr(
        OAuthClientOidcProtocol,
        "exchange_code",
        lambda self, **kwargs: {"access_token": "test-token", "id_token": "test-id-token"},
    )
    monkeypatch.setattr(OAuthClientOidcProtocol, "verify_id_token", lambda self, token, **kwargs: claims)
    monkeypatch.setattr(OAuthClientOidcProtocol, "fetch_userinfo", lambda self, token, params=None: {})
    completion = identity.complete_login(
        oauth_client, code="test-code", state_token=state_token, redirect_uri=redirect_uri
    )
    user = User._base_manager.get(pk=completion.user.pk)
    assert user.email == "first.login@example.com"
    assert not user.has_usable_password()
    account = ExternalAccount._base_manager.get(oauth_client=oauth_client, external_id="first-login")
    with system_context(reason="test.oidc.link-owner"):
        assert ExternalAccount.objects.owner_for(account) == user
    people = Person._base_manager.filter(user=user)
    assert people.count() == (1 if verified is True else 0)
    if verified is True:
        assert people.get().created_by_id == user.pk
