"""Dependency-free live bridge task constants.

Autoconfig imports this module while composing Django settings, before the app
registry is ready and before any worker-only vendor SDK should load. Keep it to
plain values: no Django models, no qrcode/Pillow, and no vendor bindings.
"""

from __future__ import annotations

RUN_SESSION_TASK = "integrate.run_bridge_session"
"""Celery task that runs one bridge's live session for its connection lifetime."""

ENSURE_SESSIONS_TASK = "integrate.ensure_bridge_sessions"
"""Celery beat task that restarts live-capable connected bridges."""

RECONCILER_INTERVAL = 60.0
"""Beat period for :data:`ENSURE_SESSIONS_TASK`."""

SESSION_START_EXPIRES = RECONCILER_INTERVAL
"""Discard an unconsumed live-session start after one reconciler tick."""
