"""Native install resources required by messaging's source-identity scenarios."""

from angee.provenance.testing import load_statement_sources


def load_source_identity() -> None:
    """Load the declared rule and its grants after a fixture host's native REBAC sync."""

    load_statement_sources("messaging")
