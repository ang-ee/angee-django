"""Settings fragments contributed when the web forms addon is installed."""

from __future__ import annotations

from typing import TypedDict

from angee.webforms.constants import PURGE_RESPONSES_TASK, REDELIVER_RESPONSES_TASK

_Settings = TypedDict(
    "_Settings",
    {
        "CELERY_BEAT_SCHEDULE:append": dict[str, dict[str, object]],
        "ANGEE_WEBFORM_TARGET_CLASSES": dict[str, str],
        "ANGEE_WEBFORM_MAX_BODY_BYTES": int,
        "ANGEE_WEBFORM_RATE_LIMIT_BURST": int,
        "ANGEE_WEBFORM_RATE_LIMIT_WINDOW": int,
        "ANGEE_WEBFORM_HONEYPOT_FIELD": str,
        "ANGEE_WEBFORM_TOKEN_HOOK": str,
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
    # The bounds of a form's public address. It is safe with no project settings:
    # a deployment may lower a bound, rename or drop the honeypot field, or name
    # one dotted token hook, and the shared guard fails closed when its cache or
    # the hook does.
    "ANGEE_WEBFORM_MAX_BODY_BYTES": 65_536,
    "ANGEE_WEBFORM_RATE_LIMIT_BURST": 10,
    "ANGEE_WEBFORM_RATE_LIMIT_WINDOW": 60,
    "ANGEE_WEBFORM_HONEYPOT_FIELD": "_website",
    "ANGEE_WEBFORM_TOKEN_HOOK": "",
}
