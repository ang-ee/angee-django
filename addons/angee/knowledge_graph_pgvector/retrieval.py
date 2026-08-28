"""The plugin's retrieval backend — a lexical stub standing in for pgvector.

Registered into ``ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES`` under the ``pgvector`` key
by :mod:`~angee.knowledge_graph_pgvector.autoconfig`, so a vault selects it with
``retrieval_class = "pgvector"`` and the knowledge ``search_pages`` resolver
dispatches to it through the vault's public ``retrieval_for`` seam — no edit to
the knowledge addon.

This is a skeleton: it **subclasses the bundled lexical backend** and inherits
its title/body ``icontains`` search, so the seam is exercised end to end while no
real embeddings exist yet. A production plugin overrides :meth:`search` with an
approximate-nearest-neighbour query over an embedding column it adds to
``knowledge.Page`` (a model ``extends`` + its own migration and pgvector index).
"""

from __future__ import annotations

from angee.knowledge.retrieval import LexicalRetrievalBackend


class PgvectorRetrievalBackend(LexicalRetrievalBackend):
    """Stub semantic backend: inherits lexical search until embeddings land.

    Concrete registry leaf (``key="pgvector"``). A real implementation keeps the
    registration shape and replaces :meth:`search` with a vector-similarity query;
    the embedding column, its backfill, and the ANN index are this plugin's
    responsibility (a migration), never the knowledge addon's.
    """

    key = "pgvector"
    label = "Pgvector (semantic)"
