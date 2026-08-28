"""OIDC OAuth provider presets contributed by the login addon."""

from __future__ import annotations

from angee.integrate.oauth.providers import OAuthProviderType


class GenericOidc(OAuthProviderType):
    """Generic OpenID Connect provider preset.

    Ships the defaults that let a configured-and-enabled OIDC provider sign existing
    users in out of the box: the standard OIDC scopes + claim mappings, plus
    link-by-verified-email. Auto-provisioning new users (``create_on_login``) stays
    off by default — a per-client opt-in, ideally with an ``allowed_email_domains``
    gate — so the framework default is fail-closed.
    """

    key = "generic_oidc"
    label = "Generic OIDC"
    category = "oidc"
    icon = "auth"
    defaults = {
        "login_enabled": True,
        "supports_pkce": True,
        "supports_refresh": True,
        "link_on_email_match": True,
        "create_on_login": False,
        "scopes_catalogue": ["openid", "profile", "email", "offline_access"],
        "default_scopes": ["openid", "profile", "email"],
        "external_id_claim": "sub",
        "email_claim": "email",
        "display_name_claim": "name",
        "avatar_url_claim": "picture",
    }


class GoogleType(GenericOidc):
    """Google OpenID Connect provider preset."""

    key = "google"
    label = "Google"
    icon = "google"
    defaults = {
        "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
        "issuer": "https://accounts.google.com",
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        "authorize_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "https://www.googleapis.com/oauth2/v2/userinfo",
        "authorize_params": {"access_type": "offline"},
        "external_id_claim": "sub",
        "email_claim": "email",
        "display_name_claim": "name",
        "avatar_url_claim": "picture",
        "scopes_catalogue": [
            "openid",
            "profile",
            "email",
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ],
        "default_scopes": [
            "openid",
            "profile",
            "email",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
        ],
    }
