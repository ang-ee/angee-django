"""Angee's source base and role factory use native composition declarations."""

from django_compose.models import Composable

from angee.base.models.base import AngeeModel
from angee.base.models.roles import role_anchor


def test_source_base_inherits_native_composable_without_legacy_defaults() -> None:
    assert issubclass(AngeeModel, Composable)
    assert not {"runtime", "extends", "requires_parent"}.intersection(AngeeModel.__dict__)


def test_role_anchor_declares_native_concrete_keyword() -> None:
    anchor = role_anchor("declaration_test/role", name="DeclarationTestRole", module=__name__)
    assert issubclass(anchor, Composable)
    assert anchor.__dict__["compose_concrete"] is True
    assert "runtime" not in anchor.__dict__
    assert anchor._meta.abstract and not anchor._meta.managed
    assert anchor._meta.rebac_resource_type == "declaration_test/role"
