"""Register Notion through the shared implementation selectors."""

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_NOTION_BACKEND_CLASSES": dict[str, str],
        "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.notion": str,
    },
)

SETTINGS: _Settings = {
    "ANGEE_NOTION_BACKEND_CLASSES": {
        "notion": "angee.knowledge_integrate_notion.backend.NotionBackend",
    },
    "ANGEE_OAUTH_PROVIDER_TYPE_CLASSES.notion": "angee.knowledge_integrate_notion.oauth.NotionOAuth",
}
