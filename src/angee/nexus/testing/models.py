"""Canonical concrete nexus models for the bare-Django test runtime.

Permission queries resolve the nexus backings (party edges, user cadences),
so register the models from conftest before Django creates the test database.
"""

from angee.nexus.models import Cadence as AbstractCadence
from angee.nexus.models import Tie as AbstractTie


class Tie(AbstractTie):
    """Concrete tie model used by nexus tests."""

    class Meta(AbstractTie.Meta):
        """Django model options for the canonical test tie."""

        abstract = False
        app_label = "nexus"
        db_table = "test_nexus_tie"
        rebac_resource_type = "nexus/tie"



class Cadence(AbstractCadence):
    """Concrete cadence model used by nexus tests."""

    class Meta(AbstractCadence.Meta):
        """Django model options for the canonical test cadence."""

        abstract = False
        app_label = "nexus"
        db_table = "test_nexus_cadence"
        rebac_resource_type = "nexus/cadence"
