"""Settings-backed contributors to the work task-merge transaction."""

from __future__ import annotations

from typing import Any

from angee.base.impl import resolve_hooks


def run_task_merge_contributors(source: Any, canonical: Any) -> None:
    """Invoke every registered contributor inside the caller's transaction.

    Each callable receives ``(source, canonical)`` locked Task instances. It
    must move only the rows it owns, be idempotent for that exact pair, and let
    every exception propagate. The work owner invokes this only after its base
    relation/stage/link/follower postconditions and before the surrounding atomic
    block commits, so one contributor failure rolls the entire merge back.
    """

    for contributor in resolve_hooks("ANGEE_WORK_MERGE_CONTRIBUTORS", sorted_unique=True):
        contributor(source, canonical)
