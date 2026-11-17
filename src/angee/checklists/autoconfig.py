"""Checklist filters on every checklist type's resource, and recorded results on record timelines."""

SETTINGS = {
    "ANGEE_GRAPHQL_RESOURCE_FILTERS:append": ["angee.checklists.schema.resource_filters"],
    "ANGEE_TIMELINE_KINDS:append": ["angee.checklists.timeline.timeline_kinds"],
}
