"""Canonical concrete uom models for the bare-Django test runtime.

Permission queries and the REBAC checks resolve every installed uom type,
so register the models from conftest rather than from the uom test module.
"""

from angee.uom.models import Uom as AbstractUom
from angee.uom.models import UomCategory as AbstractUomCategory
from angee.uom.models import UomRole as AbstractUomRole


class UomCategory(AbstractUomCategory):
    """Concrete unit-of-measure category used by uom tests."""

    class Meta(AbstractUomCategory.Meta):
        """Django model options for the canonical test uom category."""

        abstract = False
        app_label = "uom"
        db_table = "test_uom_category"
        rebac_resource_type = "uom/category"



class Uom(AbstractUom):
    """Concrete unit of measure used by uom tests."""

    class Meta(AbstractUom.Meta):
        """Django model options for the canonical test uom."""

        abstract = False
        app_label = "uom"
        db_table = "test_uom_uom"
        rebac_resource_type = "uom/uom"



class UomRole(AbstractUomRole):
    """Native role anchor needed by actor-scoped catalogue reads."""

    class Meta(AbstractUomRole.Meta):
        abstract = False
        managed = False
        app_label = "uom"
        rebac_resource_type = "uom/role"
