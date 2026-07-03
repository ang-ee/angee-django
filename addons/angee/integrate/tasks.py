"""Procrastinate task wrappers for the integrate bridge scheduler.

The pure due-scan lives in :mod:`angee.integrate.scheduler`; this module is the
queue seam that drives it. The periodic tick makes every *already-scheduled*
bridge's ``poll_interval``/``next_sync_at`` cadence real without each bridge
addon wiring its own dispatcher. A freshly created bridge has no ``next_sync_at``
until its first sync records one — the eager ``syncIntegration`` mutation is that
first sync and stays the on-demand path.
"""

from __future__ import annotations

from procrastinate import RetryStrategy
from procrastinate.contrib.django import app

from angee.integrate import scheduler


@app.periodic(cron="* * * * *", periodic_id="integrate.sync_due_bridges")
@app.task(name="integrate.sync_due_bridges", retry=RetryStrategy(max_attempts=3, exponential_wait=30))
def sync_due_bridges(_timestamp: int) -> None:
    """Run every bridge row whose ``next_sync_at`` is due.

    The scan uses the wall clock rather than the injected periodic timestamp so a
    tick delayed by queue backlog still picks up everything due by the time it
    actually runs; per-bridge failures are recorded as telemetry by ``run_sync``.
    """

    scheduler.run_due_bridges()
