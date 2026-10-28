"""Register Notion through the shared implementation selector."""

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_NOTION_BACKEND_CLASSES": dict[str, str],
    },
)

SETTINGS: _Settings = {
    "ANGEE_NOTION_BACKEND_CLASSES": {
        "notion": "angee.knowledge_integrate_notion.backend.NotionBackend",
    },
}
