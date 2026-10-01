"""Concrete resource ledger for native Django test database setup."""

from angee.resources import models as sources


class Resource(sources.Resource):
    """The canonical resource ledger used by source-addon tests."""

    class Meta(sources.Resource.Meta):
        """Django options for the shared resource test table."""

        abstract = False
        app_label = "resources"
        db_table = "test_resources_resource"
