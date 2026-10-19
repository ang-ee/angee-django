"""Settings fragments contributed when the timeline addon is installed."""

SETTINGS = {
    # Owners append dotted callables returning their timeline sources; see
    # ``angee.timeline.sources``.
    "ANGEE_HOOKS:append": ["ANGEE_TIMELINE_SOURCES"],
    "ANGEE_TIMELINE_SOURCES": [],
}
