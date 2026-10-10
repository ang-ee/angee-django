"""OAuth provider-type implementations selected by ``OAuthClient.provider_type``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from angee.base.impl import ImplBase

if TYPE_CHECKING:
    from angee.integrate.oauth.client import OAuthClientProtocol


class OAuthProviderType(ImplBase):
    """Base class for OAuth provider presets."""
    registry_setting = "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES"

    category = "oauth"
    label = "OAuth provider"
    icon = "auth"

    @classmethod
    def userinfo_params(cls, protocol: OAuthClientProtocol, access_token: str) -> Mapping[str, str]:
        """Provider-specific profile query parameters, computed from the current grant."""

        return {}

    @classmethod
    def refine_grant(cls, protocol: OAuthClientProtocol, tokens: dict[str, Any]) -> dict[str, Any]:
        """Refine an authorization grant before the connection stores it."""

        return tokens


class GenericOAuth2(OAuthProviderType):
    """Generic OAuth2 provider with no provider-specific endpoint defaults."""

    key = "generic_oauth2"
    label = "Generic OAuth2"
