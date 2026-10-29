"""Import-safe native PostgreSQL lock holder for spawned worker tests."""

from __future__ import annotations

from multiprocessing.connection import Connection

import psycopg


def hold_postgres_advisory_lock(
    params: dict[str, object],
    advisory_key: tuple[int, int],
    pair: bool,
    pipe: Connection,
) -> None:
    """Hold one native advisory lock until the parent process releases it."""

    with psycopg.connect(**params, autocommit=True) as database, database.cursor() as cursor:
        if pair:
            cursor.execute("SELECT pg_advisory_lock(%s, %s)", advisory_key)
        else:
            combined = advisory_key[0] << 32 | advisory_key[1]
            cursor.execute("SELECT pg_advisory_lock(%s)", (combined,))
        pipe.send("held")
        pipe.recv()
