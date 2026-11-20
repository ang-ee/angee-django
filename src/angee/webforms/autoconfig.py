"""Settings fragments contributed when the web forms addon is installed."""

from __future__ import annotations

from typing import TypedDict

from angee.webforms.constants import PURGE_RESPONSES_TASK, REDELIVER_RESPONSES_TASK

_Settings = TypedDict(
    "_Settings",
    {
        "CELERY_BEAT_SCHEDULE:append": dict[str, dict[str, object]],
        "ANGEE_WEBFORM_TARGET_CLASSES": dict[str, str],
    },
)

SETTINGS: _Settings = {
    "CELERY_BEAT_SCHEDULE:append": {
        PURGE_RESPONSES_TASK: {"task": PURGE_RESPONSES_TASK, "schedule": 86_400.0},
        REDELIVER_RESPONSES_TASK: {"task": REDELIVER_RESPONSES_TASK, "schedule": 300.0},
    },
    # What a kept response becomes. ``store`` keeps it as the record (a survey);
    # an addon that owns a record adds its own with a yamlconf dotted key, e.g.
    # ``"ANGEE_WEBFORM_TARGET_CLASSES.<key>"`` from its autoconfig.
    "ANGEE_WEBFORM_TARGET_CLASSES": {
        "store": "angee.webforms.targets.StoreTarget",
    },
}
