"""The Google OAuth provider preset."""

from __future__ import annotations

from angee.integrate.oauth.providers import OAuthProviderType


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
