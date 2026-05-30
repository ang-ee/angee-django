"""Tests for REBAC-aware model composition."""

from __future__ import annotations

from rebac import RebacMixin

from angee.base.compose.emission import _rebac_meta_source
from angee.base.models import AngeeModel


def test_every_angee_model_carries_the_rebac_mixin() -> None:
    """The shared base wires REBAC enforcement into all source models."""

    assert issubclass(AngeeModel, RebacMixin)


def test_composer_carries_rebac_binding_to_concrete_models() -> None:
    """The REBAC resource binding survives abstract -> concrete composition.

    The REBAC metaclass moves ``rebac_resource_type`` off ``Meta`` onto
    ``_meta``, so a concrete model built from an abstract source would lose it
    unless the composer re-emits it.
    """

    class GatedThing(AngeeModel):
        class Meta:
            abstract = True
            app_label = "base"
            rebac_resource_type = "demo/thing"
            rebac_id_attr = "sqid"

    assert _rebac_meta_source(GatedThing) == [
        "        rebac_resource_type = 'demo/thing'",
        "        rebac_id_attr = 'sqid'",
    ]


def test_composer_omits_rebac_binding_for_plain_models() -> None:
    """A model that declares no resource type emits no REBAC Meta lines."""

    class PlainThing(AngeeModel):
        class Meta:
            abstract = True
            app_label = "base"

    assert _rebac_meta_source(PlainThing) == []
