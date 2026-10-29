"""Settings fragments contributed when provenance is installed."""

from __future__ import annotations

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "SIMPLE_HISTORY_HISTORY_CHANGE_REASON_USE_TEXT_FIELD": bool,
        "ANGEE_GRAPHQL_WRITE_BACKEND_ADAPTER": str,
        "ANGEE_CHANGESET_MODEL": str,
        "MIDDLEWARE:append": list[str],
        "ANGEE_CELERY_TASK_CLASS": str,
        "COMPOSE_COMMAND_CONTEXT:append": list[str],
        "COMPOSE_FINGERPRINT_SETTINGS:append": list[str],
        "ANGEE_TIMELINE_KINDS:append": list[str],
    },
)

SETTINGS: _Settings = {
    "SIMPLE_HISTORY_HISTORY_CHANGE_REASON_USE_TEXT_FIELD": True,
    "ANGEE_GRAPHQL_WRITE_BACKEND_ADAPTER": "angee.provenance.graphql.write_backend",
    "ANGEE_CHANGESET_MODEL": "provenance.Changeset",
    "COMPOSE_FINGERPRINT_SETTINGS:append": ["ANGEE_CHANGESET_MODEL"],
    "MIDDLEWARE:append": ["angee.provenance.middleware.ChangesetMiddleware"],
    "ANGEE_CELERY_TASK_CLASS": "angee.provenance.tasks:Task",
    "COMPOSE_COMMAND_CONTEXT:append": ["angee.provenance.contrib.angee.context.management_context"],
    "ANGEE_TIMELINE_KINDS:append": ["angee.provenance.timeline.timeline_kinds"],
}
