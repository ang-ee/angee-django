"""Concrete attribution storage for native Django test database setup."""

from angee.provenance import models as sources


class Changeset(sources.Changeset):
    """The canonical changeset used by source-addon tests."""

    class Meta(sources.Changeset.Meta):
        abstract = False
        app_label = "provenance"
        db_table = "test_provenance_changeset"
        base_manager_name = "_angee_base"


class Rule(sources.Rule):
    """Canonical persisted statement source for the native test host."""

    class Meta(sources.Rule.Meta):
        abstract = False
        app_label = "provenance"
        db_table = "test_provenance_rule"


class Seed(sources.Seed):
    """Canonical persisted statement source for the native test host."""

    class Meta(sources.Seed.Meta):
        abstract = False
        app_label = "provenance"
        db_table = "test_provenance_seed"
