"""Shared registry contracts for the bare source test host."""

import pytest
from django.apps import apps
from django_compose.model_composition import ModelComposition

from angee.base.checks import check_ownership
from angee.base.fields.state import StateField
from angee.base.models.append_only import AppendOnlyModel
from angee.base.models.base import AngeeModel
from angee.base.models.merge_references import MergeContractMixin
from angee.base.models.trash import TrashMixin
from angee.messaging.testing.models import Reaction, Recipient

pytestmark = pytest.mark.shared_inputs


def test_installed_trash_models_compose_the_trash_scopes() -> None:
    models_ = [model for model in apps.get_models() if issubclass(model, TrashMixin)]
    assert models_
    assert all(not [error for error in model.check() if error.id == "angee.E036"] for model in models_)


def test_installed_append_only_default_managers_compose_guard() -> None:
    models = [model for model in apps.get_models() if issubclass(model, AppendOnlyModel)]
    assert models
    assert all(not [error for error in model.check() if error.id == "angee.E034"] for model in models)


def test_installed_models_spell_ownership_one_way() -> None:
    """No installed definition names a relation owner other than OwnerMixin's column.

    Test modules register their own models in the shared registry, so this
    asserts the spelling rule alone; E022 and E027 have their focused tests.
    """

    assert [error.msg for error in check_ownership() if error.id == "angee.E045"] == []


def test_concrete_angee_models_keep_the_manager_queryset_canon() -> None:
    """Concrete Angee models expose the inherited public-id and aggregate scopes."""

    missing: dict[str, list[str]] = {}
    for model in apps.get_models():
        if model._meta.abstract or not issubclass(model, AngeeModel):
            continue
        queryset = model._default_manager.all()
        missing_methods = [
            method_name
            for method_name in ("from_public_id", "scoped_for_aggregate")
            if not callable(getattr(queryset, method_name, None))
        ]
        if missing_methods:
            missing[model._meta.label] = missing_methods

    assert missing == {}


def test_active_model_state_fields_pass_checks() -> None:
    """Concrete composed fields satisfy the optional-state declaration rule."""

    errors = [
        error
        for model in apps.get_models()
        for field in model._meta.get_fields()
        if isinstance(field, StateField)
        for error in field.check()
    ]
    assert not errors


def test_framework_runtime_models_do_not_use_naive_irregular_plurals() -> None:
    """Every composed framework model with an irregular ending declares a real plural."""

    composition = ModelComposition.discover(apps.get_app_configs())
    for candidate in composition.ordered_models:
        if not candidate.__module__.startswith("angee."):
            continue
        singular = str(candidate._meta.verbose_name)
        if singular.endswith(("y", "s", "x", "ch", "sh")):
            assert str(candidate._meta.verbose_name_plural) != f"{singular}s", (
                f"{candidate._meta.label} uses Django's naive plural"
            )


def test_installed_merge_identities_are_declared_unique_sets() -> None:
    """Edges and plain pointing rows (a message's reactions) declare their identity alike."""

    pointing = [model for model in apps.get_models() if issubclass(model, MergeContractMixin)]
    assert {Recipient, Reaction} <= set(pointing)
    assert not [error for model in pointing for error in model.check() if error.id == "angee.E037"]
