"""Stamp bounded, language-neutral PostgreSQL full-text indexes."""

from typing import Any

from django.contrib.postgres.search import SearchVector
from django.db import connections, models

from angee.base.serialization import strip_null_bytes

SEARCH_CONFIG = "simple"
SEARCH_MAX_BYTES = 512 * 1024


def stamp_search_vector(rows: models.QuerySet[Any], text: str) -> None:
    """Set ``search`` on derived rows; leave it NULL on the SQLite test floor.

    PostgreSQL rejects tsvector input over 1 MiB. A bounded UTF-8 prefix leaves
    headroom for the vector while preserving the canonical text at its owner.
    Call inside the transaction that inserts or refreshes the derived rows.
    """

    if connections[rows.db].vendor == "postgresql":
        bounded = strip_null_bytes(text).encode("utf-8")[:SEARCH_MAX_BYTES].decode("utf-8", errors="ignore")
        rows.update(search=SearchVector(models.Value(bounded), config=SEARCH_CONFIG))
