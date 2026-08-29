"""OAuth provider-type implementations selected by ``OAuthClient.provider_type``."""

from __future__ import annotations

from angee.base.impl import ImplBase


class OAuthProviderType(ImplBase):
    """Base class for OAuth provider presets."""

    category = "oauth"
    label = "OAuth provider"
    icon = "auth"


class GenericOAuth2(OAuthProviderType):
    """Generic OAuth2 provider with no provider-specific endpoint defaults."""

    key = "generic_oauth2"
    label = "Generic OAuth2"
