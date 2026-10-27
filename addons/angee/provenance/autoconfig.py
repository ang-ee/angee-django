"""Settings fragments contributed when provenance is installed."""

SETTINGS = {
    "SIMPLE_HISTORY_HISTORY_CHANGE_REASON_USE_TEXT_FIELD": True,
    "ANGEE_GRAPHQL_WRITE_BACKEND_ADAPTER": "angee.provenance.graphql.write_backend",
    "ANGEE_CHANGESET_MODEL": "provenance.Changeset",
    "MIDDLEWARE:append": ["angee.provenance.middleware.ChangesetMiddleware"],
    "ANGEE_CELERY_TASK_CLASS": "angee.provenance.tasks:Task",
    "ANGEE_MANAGEMENT_CONTEXT": "angee.provenance.contrib.angee.context.management_context",
    "ANGEE_TIMELINE_KINDS:append": ["angee.provenance.timeline.timeline_kinds"],
}
