"""The Google OAuth provider preset."""

from __future__ import annotations

from collections.abc import Mapping

from angee.integrate.oauth.providers import OAuthProviderType
from angee.integrate.oauth.state import StateFlow


class GoogleType(OAuthProviderType):
    """Google's OAuth 2.0 and OpenID Connect endpoints, claims and scope catalogue.

    The catalogue lists every scope a Google consumer may request through one
    Google client. ``include_granted_scopes`` asks Google for incremental
    authorization, so a consumer that adds a scope keeps the scopes the account
    granted before; ``access_type=offline`` asks for a refresh token. The preset
    carries no login policy.
    """

    key = "google"
    label = "Google"
    category = "oidc"
    icon = "google"

    @classmethod
    def issuers(cls, issuer: str) -> tuple[str, ...]:
        """Google signs ID tokens as ``https://accounts.google.com`` or ``accounts.google.com``."""

        return tuple(dict.fromkeys((issuer, issuer.removeprefix("https://"))))

    @classmethod
    def authorize_params(cls, flow: StateFlow, configured: Mapping[str, str], *, consent: bool) -> dict[str, str]:
        """Let a connect choose among the user's Google accounts, and ask for consent on request.

        Google issues a refresh token on the consent screen only, so a connect
        that received none restarts with ``prompt=consent``. Sign-in keeps
        Google's own prompt.
        """

        params = super().authorize_params(flow, configured, consent=consent)
        prompts = params.get("prompt", "").split()
        if flow == StateFlow.CONNECT:
            prompts.append("select_account")
        if consent:
            prompts.append("consent")
        if prompts:
            params["prompt"] = " ".join(dict.fromkeys(prompts))
        return params
    defaults = {
        "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
        "issuer": "https://accounts.google.com",
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        "authorize_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "revoke_endpoint": "https://oauth2.googleapis.com/revoke",
        "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
        "supports_pkce": True,
        "supports_refresh": True,
        "authorize_params": {"access_type": "offline", "include_granted_scopes": "true"},
        "external_id_claim": "sub",
        "email_claim": "email",
        "display_name_claim": "name",
        "avatar_url_claim": "picture",
        "scopes_catalogue": [
            "openid",
            "email",
            "profile",
            # Gmail over IMAP and SMTP (a restricted scope).
            "https://mail.google.com/",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/youtube.force-ssl",
            # Gemini through the Google Cloud APIs.
            "https://www.googleapis.com/auth/cloud-platform",
        ],
        "default_scopes": ["openid", "email", "profile"],
    }
