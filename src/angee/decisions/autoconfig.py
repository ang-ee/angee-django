"""Generic record attention and related counts supplied by decisions."""

SETTINGS = {
    "ANGEE_GRAPHQL_RESOURCE_FILTERS:append": ["angee.decisions.attention.resource_filters"],
    "ANGEE_GRAPHQL_RELATED_COUNTS:append": ["angee.decisions.schema.related_counts"],
    "ANGEE_TIMELINE_KINDS:append": ["angee.decisions.timeline.timeline_kinds"],
}
