"""Settings fragments contributed by the WhatsApp channel backend addon."""

from __future__ import annotations

from angee.messaging_integrate_whatsapp.constants import ENSURE_SESSIONS_TASK, RECONCILER_INTERVAL

SETTINGS = {
    # Contribute the WhatsApp backend into the channel backend registry. Dotted
    # key so it merges into messaging's default rather than replacing it.
    "ANGEE_CHANNEL_BACKEND_CLASSES.whatsapp": "angee.messaging_integrate_whatsapp.backend.WhatsAppChannelBackend",
    # The session reconciler: restarts a live-desired channel's session after a
    # worker crash or redeploy (the session task's advisory lock is the gate, so
    # the tick is safely idempotent). Rides the default queue — only the session
    # tasks themselves run on the dedicated ``whatsapp`` queue. Task name and
    # period are owned by ``constants`` so the session-start ``expires`` cannot
    # drift from this schedule.
    "CELERY_BEAT_SCHEDULE:append": {
        ENSURE_SESSIONS_TASK: {
            "task": ENSURE_SESSIONS_TASK,
            "schedule": RECONCILER_INTERVAL,
        },
    },
}
"""Django settings contributed when the WhatsApp channel addon is installed."""
