"""Settings fragments contributed when the work addon is installed."""

from __future__ import annotations

SETTINGS = {
    "CELERY_BEAT_SCHEDULE:append": {
        "work.wake_due_snoozes": {
            "task": "work.wake_due_snoozes",
            "schedule": 60.0,
        },
        "work.generate_cycles": {
            "task": "work.generate_cycles",
            "schedule": 3600.0,
        },
    },
}
