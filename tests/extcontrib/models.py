"""Example owners of roster authority and record-derived decision access."""

from django.db import models

from angee.base.evidence import DerivedFromRelation
from angee.base.models import AngeeDataModel, role_anchor

Role = role_anchor("extcontrib/role", name="Role")


class Record(AngeeDataModel):
    """A concerned record exposing retained links through a typed query path."""

    sqid_prefix = "xrc_"
    name = models.CharField(max_length=100)
    decision_records = DerivedFromRelation("decisions.DecisionRecord", related_query_name="extcontrib_record")

    class Meta(AngeeDataModel.Meta):
        abstract = False
        db_table = "test_extcontrib_record"
        rebac_resource_type = "extcontrib/record"
