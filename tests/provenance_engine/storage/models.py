"""Concrete native storage for the provenance engine test host."""

from angee.provenance.engine.base import AbstractChangeset


class Changeset(AbstractChangeset):
    """Preserve the library's default table and swappable identity in tests."""

    class Meta(AbstractChangeset.Meta):
        abstract = False
        app_label = "provenance"
        swappable = "PROVENANCE_CHANGESET_MODEL"
