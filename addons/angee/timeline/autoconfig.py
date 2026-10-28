"""Settings fragments contributed when the timeline addon is installed."""

SETTINGS: dict[str, list[str]] = {
    # Owners append dotted callables returning their timeline kinds; see
    # ``angee.timeline.kinds``.
    "ANGEE_TIMELINE_KINDS": [],
}
