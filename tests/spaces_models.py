"""Canonical concrete spaces models shared by bare-Django test runtimes."""

from __future__ import annotations

from angee.spaces.models import Group as AbstractGroup
from angee.spaces.models import Membership as AbstractMembership


class Group(AbstractGroup):
    """Concrete shared group used by tests without a composed runtime."""

    class Meta(AbstractGroup.Meta):
        """Django options for the canonical test group."""

        abstract = False
        app_label = "spaces"
        db_table = "test_spaces_group"
        rebac_resource_type = "spaces/group"
        rebac_id_attr = "sqid"


class Membership(AbstractMembership):
    """Concrete roster row used by tests without a composed runtime."""

    class Meta(AbstractMembership.Meta):
        """Django options for the canonical test membership."""

        abstract = False
        app_label = "spaces"
        db_table = "test_spaces_membership"
        rebac_resource_type = "spaces/membership"
        rebac_id_attr = "sqid"
