"""Contribute the in-transaction message source to workflows' registry."""

SETTINGS = {
    "ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES.message_ingested": "angee.workflows_messaging.sources.MessageIngested",
}
