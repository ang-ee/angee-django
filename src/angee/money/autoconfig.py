"""Settings for the money addon's rate permission backing."""

SETTINGS = {
    "ANGEE_MONEY_REFERENCE_CURRENCY": None,
    # The rate's filtered constant crosses context_content_type; index writes
    # must observe ContentType changes as well as tracked money models.
    "REBAC_TRACKED_MODELS": ["contenttypes.ContentType"],
}
