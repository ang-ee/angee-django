"""A company-scoped, create-gated document for the create-gate regression tests.

``ScopedDoc`` composes :class:`~angee.iam.models.CompanyScopedMixin` (the blank-
on-input ``company`` FK defaulted from the actor's sole membership on ``save``)
and gates its ``create`` on ``company->member`` in the adjacent
``permissions.zed`` — the company-arm shape that fail-closes unless the create
preflight is evaluated against the defaulted company (arp.calendar.event is the
first real consumer of this shape). ``linesdemo``/``chatterdemo`` gate ``create``
on ``authenticated``, so neither exercises the company-arm gate; this concrete
row does, in a real installed app so pytest-django builds its table and
``rebac sync`` loads its definition.
"""

from __future__ import annotations

from django.db import models

from angee.base.models import AngeeDataModel
from angee.iam.models import CompanyScopedMixin


class ScopedDoc(CompanyScopedMixin, AngeeDataModel):
    """A company-scoped document whose ``create`` rides ``company->member``."""

    sqid_prefix = "scd_"

    title = models.CharField(max_length=200, blank=True, default="")

    class Meta(AngeeDataModel.Meta):
        """Concrete company-scoped, create-gated document for the gate tests."""

        abstract = False
        app_label = "scopedemo"
        db_table = "test_scopedemo_doc"
        rebac_resource_type = "scopedemo/doc"
        rebac_id_attr = "sqid"
