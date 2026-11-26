"""Settings fragments contributed when the decision mail addon is installed."""

from __future__ import annotations

from celery.schedules import crontab

from angee.decisions_messaging.constants import DIGEST_HOUR, MAIL_DIGESTS_TASK

SETTINGS = {
    # The daily digest, once a day at a fixed hour. A run no worker picks up within one
    # tick is dropped, as decisions' reminder sweep drops its own; the next one mails.
    "CELERY_BEAT_SCHEDULE:append": {
        MAIL_DIGESTS_TASK: {
            "task": MAIL_DIGESTS_TASK,
            "schedule": crontab(minute=0, hour=DIGEST_HOUR),
            "options": {"expires": 86_400},
        },
    },
}
"""Django settings contributed when the decision mail addon is installed."""
