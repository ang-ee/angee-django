"""The generic OIDC login preset contributed by the login addon.

Vendor presets (``integrate_google``) carry no login policy; this addon sets it on
the vendor rows it signs users in with, through its install seed.
"""

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

