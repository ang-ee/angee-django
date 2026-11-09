"""Database capability required by the pgvector retrieval selection."""

from django.core.exceptions import ImproperlyConfigured
from django.db import connection


def require_pgvector() -> None:
    """Refuse unsupported databases and a missing extension, without fallback."""

    if connection.vendor != "postgresql":
        raise ImproperlyConfigured("The pgvector retrieval backend requires PostgreSQL with the vector extension.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        if not cursor.fetchone()[0]:
            raise ImproperlyConfigured(
                "The vector extension is missing. Run migrate with extension-creation privileges, "
                "or ask a database administrator to CREATE EXTENSION vector first."
            )
