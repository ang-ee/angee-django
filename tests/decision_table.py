"""Decision-table rows for ``rebac.testing.assert_decisions``, written as allowed and denied seats."""

from collections.abc import Callable
from typing import Any


def seat_cells(actor: Callable[[str], Any], *rows: tuple[Any, str, str, str]) -> list[tuple[Any, str, Any, bool]]:
    """Expand ``(resource, action, "allowed seats", "denied seats")`` rows into ``assert_decisions`` checks.

    Seats are space-separated names that ``actor`` resolves to subjects.
    """
    return [
        (actor(seat), action, resource, allowed)
        for resource, action, *seats in rows
        for names, allowed in zip(seats, (True, False), strict=True)
        for seat in names.split()
    ]
