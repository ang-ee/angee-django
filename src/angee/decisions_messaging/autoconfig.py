"""Settings fragments contributed when the decision mail addon is installed."""

from __future__ import annotations

from typing import TypedDict

from celery.schedules import crontab

from angee.decisions_messaging.constants import DIGEST_HOUR, MAIL_DIGESTS_TASK

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_DECISIONS_MESSAGING_AUDIENCES": dict[str, str],
        "CELERY_BEAT_SCHEDULE:append": dict[str, dict[str, object]],
    },
)

SETTINGS: _Settings = {
    # Who a question's mail goes to, by its decision kind; "*" covers every kind not
    # named. "deciders" (those who may answer and read it) or "administrators" (the
    # active platform administrators who read it). A deployment that mails its
    # administrators by default and one kind to its deciders sets
    # {"*": "administrators", "<kind>": "deciders"}. Checked at start-up
    # (decisions_messaging.E001).
    "ANGEE_DECISIONS_MESSAGING_AUDIENCES": {"*": "deciders"},
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
