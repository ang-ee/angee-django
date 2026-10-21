"""Settings fragments required by the knowledge addon."""

from __future__ import annotations

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_IMPL_REGISTRIES:append": list[str],
        "ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES": dict[str, str],
    },
)

SETTINGS: _Settings = {
    "ANGEE_IMPL_REGISTRIES:append": ["angee.knowledge.retrieval.RetrievalBackend"],
    # Vaults select their retrieval backend with a vault-owned ``retrieval_class``
    # field. ``lexical`` is the default title/body ``icontains`` search. A semantic
    # plugin (pgvector/graphrag) contributes its own key through autoconfig without
    # editing this addon.
    "ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES": {
        "lexical": "angee.knowledge.retrieval.LexicalRetrievalBackend",
    },
}
"""Django settings contributed when the knowledge addon is installed."""
