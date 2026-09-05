"""Canonical concrete Integration shared by bare-Django test fixtures."""

from angee.integrate.models import Integration as AbstractIntegration


class Integration(AbstractIntegration):
    """Concrete integration used by source-addon tests."""

    class Meta(AbstractIntegration.Meta):
        """Django model options for the canonical test integration."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_integration"
        rebac_resource_type = "integrate/integration"
        rebac_id_attr = "sqid"
