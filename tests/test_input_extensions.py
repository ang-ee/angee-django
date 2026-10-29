"""Cooperative input owners share one fieldless, fail-fast terminal."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db.migrations.state import ModelState
from django.test.utils import isolate_apps
from rebac import RebacMixin

from angee.base.models import AngeeModel, InputExtensionsMixin
from angee.tags.models import TaggedModel


def test_angee_model_reuses_the_input_terminal() -> None:
    assert AngeeModel.apply_input_extensions is InputExtensionsMixin.apply_input_extensions
    assert InputExtensionsMixin._meta.fields == ()


@isolate_apps()
def test_input_owners_delegate_once_in_mro_order_and_refuse_unknown_values() -> None:
    seen = []

    class FirstOwner(InputExtensionsMixin):
        class Meta:
            abstract = True

        def apply_input_extensions(self, *, first: str | None = None, **values: object) -> None:
            if first is not None:
                seen.append(("first", first))
            super().apply_input_extensions(**values)

    class SecondOwner(InputExtensionsMixin):
        class Meta:
            abstract = True

        def apply_input_extensions(self, *, second: str | None = None, **values: object) -> None:
            if second is not None:
                seen.append(("second", second))
            super().apply_input_extensions(**values)

    class Host(FirstOwner, SecondOwner, RebacMixin):
        class Meta:
            app_label = "input_probes"

    host = Host()
    host.apply_input_extensions(first="a", second="b")
    assert seen == [("first", "a"), ("second", "b")]
    host.apply_input_extensions()
    assert seen == [("first", "a"), ("second", "b")]
    with pytest.raises(ImproperlyConfigured, match="no owner for input extension values: extra, other"):
        host.apply_input_extensions(other=True, extra=True)


@isolate_apps()
def test_tagged_plain_rebac_host_keeps_its_native_model_fields() -> None:
    class Plain(RebacMixin):
        class Meta:
            app_label = "input_probes"

    class Tagged(TaggedModel, RebacMixin):
        class Meta:
            app_label = "input_probes"

    plain = ModelState.from_model(Plain)
    tagged = ModelState.from_model(Tagged)
    assert {name: field.deconstruct()[1:] for name, field in plain.fields.items()} == {
        name: field.deconstruct()[1:] for name, field in tagged.fields.items()
    }
    assert not issubclass(Tagged, AngeeModel)
    Tagged().apply_input_extensions()
    with pytest.raises(ImproperlyConfigured, match="no owner for input extension values: extra"):
        Tagged().apply_input_extensions(extra=True)
