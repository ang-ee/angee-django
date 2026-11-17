"""Generic record attention, related counts and the reminder sweep supplied by decisions."""

SETTINGS = {
    "ANGEE_GRAPHQL_RESOURCE_FILTERS:append": ["angee.decisions.attention.resource_filters"],
    "ANGEE_GRAPHQL_RELATED_COUNTS:append": ["angee.decisions.schema.related_counts"],
    "ANGEE_TIMELINE_KINDS:append": ["angee.decisions.timeline.timeline_kinds"],
    "CELERY_BEAT_SCHEDULE:append": {
        "decisions.remind_due": {"task": "decisions.remind_due", "schedule": 60.0, "options": {"expires": 60}},
    },
}
