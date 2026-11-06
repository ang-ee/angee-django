"""Generic record attention and related edges supplied by decisions."""

SETTINGS = {
    "ANGEE_GRAPHQL_RESOURCE_FILTERS:append": ["angee.decisions.attention.resource_filters"],
    "ANGEE_GRAPHQL_RECORD_EDGES:append": ["angee.decisions.schema.record_edges"],
    "ANGEE_TIMELINE_KINDS:append": ["angee.decisions.timeline.timeline_kinds"],
}
