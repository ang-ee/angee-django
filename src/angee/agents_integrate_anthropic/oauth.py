"""Anthropic-owned OAuth grant parameter policy."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from angee.integrate.oauth.providers import OAuthProviderType


class AnthropicOAuth(OAuthProviderType):
    """Keep custom authorization lifetimes out of Anthropic refresh grants."""

    key = "anthropic"
    label = "Anthropic"

    @classmethod
    def token_params(cls, grant_type: str, configured: Mapping[str, Any]) -> dict[str, Any]:
        params = dict(configured)
        if grant_type == "refresh_token":
            params.pop("expires_in", None)
        return params
