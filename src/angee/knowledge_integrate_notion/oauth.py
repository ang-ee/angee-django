"""Notion's public-integration OAuth preset."""

from angee.integrate.oauth.providers import OAuthProviderType


class NotionOAuth(OAuthProviderType):
    """Authorize a user's workspace pages; the token response identifies the authorization.

    Notion authenticates its token endpoint with HTTP Basic client credentials. Its
    profile read (`/v1/users/me`) requires a `Notion-Version` header the generic
    userinfo read does not send, so the account is labelled from the token response.
    """

    key = "notion"
    label = "Notion"
    icon = "integration"
    defaults = {
        "authorize_endpoint": "https://api.notion.com/v1/oauth/authorize",
        "token_endpoint": "https://api.notion.com/v1/oauth/token",
        "token_endpoint_auth_method": "client_secret_basic",
        "supports_pkce": False,
        "default_scopes": [],
        "scopes_catalogue": [],
        "authorize_params": {"owner": "user"},
        "token_identity_keys": ["bot_id", "workspace_id", "workspace_name", "owner"],
        "external_id_claim": "bot_id",
        "email_claim": "owner.user.person.email",
        "display_name_claim": "workspace_name",
        "avatar_url_claim": "owner.user.avatar_url",
    }
