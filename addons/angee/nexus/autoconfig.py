"""Settings fragments required by the nexus addon."""

from __future__ import annotations

SETTINGS = {
    # Three linear deliberate-interaction passes keep the derived pair graph
    # current without per-message write amplification.
    "CELERY_BEAT_SCHEDULE:append": {
        "nexus.recompute_ties": {
            "task": "nexus.recompute_ties",
            "schedule": 3600.0,
        },
    },
}
