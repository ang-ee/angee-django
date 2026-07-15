"""Shared constants — one owner for facts that autoconfig and code both name.

Kept dependency-free so ``autoconfig.py`` can import it at settings-compose time
without pulling Django models or the vendor binding.
"""

from __future__ import annotations

RUN_SESSION_TASK = "whatsapp.run_session"
"""Celery task that runs one channel's live session for its connection lifetime."""

ENSURE_SESSIONS_TASK = "whatsapp.ensure_sessions"
"""Celery beat task that restarts any live-desired channel with no running session."""

SESSION_QUEUE = "whatsapp"
"""Dedicated queue whose threads-pool worker hosts the live sessions."""

RECONCILER_INTERVAL = 60.0
"""Beat period for :data:`ENSURE_SESSIONS_TASK`. The session-start ``expires``
derives from this so an undelivered start never outlives one reconciler tick —
change it in one place and both the schedule and the expiry follow."""
