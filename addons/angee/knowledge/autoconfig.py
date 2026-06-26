"""Settings fragments required by the knowledge addon."""

from __future__ import annotations

SETTINGS = {
    # Vaults select their retrieval backend with a vault-owned ``retrieval_class``
    # field. ``lexical`` is the default title/body ``icontains`` search. A semantic
    # plugin (pgvector/graphrag) contributes its own key through autoconfig without
    # editing this addon.
    "ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES": {
        "lexical": "angee.knowledge.retrieval.LexicalRetrievalBackend",
    },
}
"""Django settings contributed when the knowledge addon is installed."""
