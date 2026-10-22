"""Settings fragments contributed when the timeline addon is installed."""

SETTINGS = {
    # Owners append dotted callables returning their timeline kinds; see
    # ``angee.timeline.kinds``.
    "ANGEE_HOOKS:append": ["ANGEE_TIMELINE_KINDS"],
    "ANGEE_TIMELINE_KINDS": [],
}
