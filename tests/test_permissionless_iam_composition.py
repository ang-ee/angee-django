"""Optional IAM composes native protection onto neutral model contracts.

Run independently with --noconftest --ds=tests.iam_only_settings.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest
from django.conf import settings

if settings.SETTINGS_MODULE != "tests.iam_only_settings":
    pytest.skip("Run with its documented minimal source-fixture settings.", allow_module_level=True)
from django.apps import apps
from django_compose.model_composition import ModelComposition
from django_compose.rendering import render_models

from angee.base.models.base import AngeeModel
from angee.iam.models.base import IAMProtectionMixin, ProtectedModel
from angee.iam.querysets import ProtectedQuerySet, ProtectedUnscopedQuerySet
from tests.permissionless_app.models import SourceEntry


def test_iam_lifecycle_does_not_import_optional_capability_contributions():
    for addon in ("graphql", "resources", "web", "provenance"):
        assert not apps.is_installed(f"angee.{addon}")
        assert not any(name == f"angee.{addon}" or name.startswith(f"angee.{addon}.") for name in sys.modules)
    assert not any(name.startswith("angee.iam.schema") for name in sys.modules)


def test_native_abstract_target_and_queryset_donor_protect_the_neutral_source(monkeypatch):
    assert IAMProtectionMixin.__dict__["compose_extends"] == "base.angeemodel"
    assert not issubclass(SourceEntry, ProtectedModel)
    assert issubclass(SourceEntry, AngeeModel)
    composition = ModelComposition.discover(apps.get_app_configs())
    assert IAMProtectionMixin in composition.donors(SourceEntry)
    querysets = composition.queryset_contributions(SourceEntry)
    assert ProtectedQuerySet in querysets["objects"][1]
    assert ProtectedUnscopedQuerySet in querysets["_angee_base"][1]
    emitted = render_models(composition, "permissionless")
    assert "IAMProtectionMixin" in emitted
    generated = ModuleType("tests.iam_compiled_probe")
    monkeypatch.setitem(sys.modules, generated.__name__, generated)
    exec(compile(emitted, "<native emitted IAM source>", "exec"), generated.__dict__)
    concrete = generated.SourceEntry
    assert issubclass(concrete, ProtectedModel)
    assert concrete._default_manager.name == "objects"
    assert concrete._base_manager.name == "_angee_base"
    assert isinstance(concrete.objects.get_queryset(), ProtectedQuerySet)
    assert isinstance(concrete._base_manager.get_queryset(), ProtectedUnscopedQuerySet)
    assert isinstance(concrete.objects, type(SourceEntry._meta.managers_map["objects"]))
    assert "ProtectedQuerySet" in emitted


def test_mixed_native_capabilities_keep_single_model_and_queryset_owners(monkeypatch):
    from django_compose.declarations import model_declaration

    from angee.iam.build import PermissionIntegration
    from angee.iam.models.derived import ProtectedDerivedFrom
    from angee.iam.models.locks import ProtectedRowLockMixin
    from angee.iam.models.merge import ProtectedMergeableMixin
    from tests.permissioned_app import models as source

    composition = ModelComposition.discover(apps.get_app_configs())
    declarations = {
        model: model_declaration(composition, model, (PermissionIntegration(),)) for model in composition.ordered_models
    }
    emitted = render_models(composition, "permissioned_app", declarations=declarations)
    generated = ModuleType("tests.iam_mixed_compiled_probe")
    monkeypatch.setitem(sys.modules, generated.__name__, generated)
    exec(compile(emitted, "<native emitted IAM capabilities>", "exec"), generated.__dict__)
    for name, owner in (
        ("Evidence", ProtectedDerivedFrom),
        ("Locked", ProtectedRowLockMixin),
        ("Mergeable", ProtectedMergeableMixin),
    ):
        model = getattr(generated, name)
        authored = getattr(source, name)
        assert issubclass(model, owner)
        assert model._base_manager.name == "_angee_base"
        assert model._default_manager.name == "objects"
        assert isinstance(model.objects, type(authored._meta.managers_map["objects"]))
        assert model._meta.rebac_resource_type == authored._meta.rebac_resource_type


@pytest.fixture
def pinned_actor_schema():
    from rebac.backends import backend, reset_backend
    from rebac.schema import parse_zed

    reset_backend()
    backend().set_schema(
        parse_zed("""
        definition auth/user {}
        definition probe/merge {
            relation reader: auth/user
            relation editor: auth/user
            relation remover: auth/user
            permission read = reader
            permission write = editor
            permission delete = remover
        }
        definition probe/trash {
            relation editor: auth/user
            permission read = editor
            permission write = editor
        }
    """)
    )
    yield
    reset_backend()


@pytest.mark.django_db
@pytest.mark.parametrize(("survivor_allowed", "message"), ((False, "kept"), (True, "every record")))
def test_pinned_actor_merge_checks_survivor_and_merged_rows_inside_sudo(pinned_actor_schema, survivor_allowed, message):
    from rebac import (
        PermissionDenied,
        RelationshipTuple,
        SubjectRef,
        system_context,
        to_object_ref,
        write_relationships,
    )

    from tests.iam_only_models import MergeTarget

    actor = SubjectRef.of("auth/user", "denied")
    with system_context(reason="minimal protected merge fixture"):
        kept = MergeTarget.objects.create()
        merged = MergeTarget.objects.create()
        grants = [RelationshipTuple(to_object_ref(row), "reader", actor) for row in (kept, merged)]
        if survivor_allowed:
            grants.append(RelationshipTuple(to_object_ref(kept), "editor", actor))
        write_relationships(grants)
        kept.with_actor(actor)
        assert merged.with_actor(actor).has_access("read"), "merged direct read"
        assert list(kept.merge_candidates().filter(pk=merged.pk).values_list("pk", flat=True)) == [merged.pk]
        assert kept.merge_candidates().effective_actor() == (actor, False)
        with pytest.raises(PermissionDenied, match=message):
            kept.merge(records=[merged.public_id])
        assert MergeTarget._base_manager.filter(pk__in=(kept.pk, merged.pk)).count() == 2
        assert not MergeTarget._base_manager.exclude(merged_into_id=None).exists()


@pytest.mark.django_db
def test_pinned_actor_trash_replay_cannot_inherit_ambient_sudo(pinned_actor_schema):
    from rebac import PermissionDenied, SubjectRef, system_context

    from tests.iam_only_models import TrashTarget

    actor = SubjectRef.of("auth/user", "denied")
    with system_context(reason="minimal protected trash fixture"):
        row = TrashTarget.objects.create()
        with pytest.raises(PermissionDenied):
            row.with_actor(actor).revert_removal(reason="replay")
        row.refresh_from_db()
        assert not row.is_trashed


@pytest.mark.django_db
def test_pinned_actor_cannot_merge_an_unreadable_candidate_inside_sudo(pinned_actor_schema):
    from django.core.exceptions import ValidationError
    from rebac import SubjectRef, system_context

    from tests.iam_only_models import MergeTarget

    with system_context(reason="minimal protected hidden merge candidate"):
        kept = MergeTarget.objects.create()
        merged = MergeTarget.objects.create()
        with pytest.raises(ValidationError, match="no longer exist"):
            kept.with_actor(SubjectRef.of("auth/user", "denied")).merge(records=[merged.public_id])
        assert MergeTarget._base_manager.filter(pk__in=(kept.pk, merged.pk)).count() == 2


def test_field_free_protected_persistence_scopes_native_subquery_construction():
    from django.db import models
    from rebac import RebacMixin
    from rebac.errors import MissingActorError

    from angee.iam.managers import ProtectedManager, ProtectedUnscopedManager
    from angee.iam.models.base import ProtectedPersistenceMixin
    from tests.iam_only_models import APIEntry, ProtectedMembership

    assert issubclass(ProtectedMembership, ProtectedPersistenceMixin)
    assert not issubclass(ProtectedMembership, RebacMixin)
    assert [field.name for field in ProtectedMembership._meta.local_fields] == ["id", "entry", "label"]
    assert type(ProtectedMembership._default_manager) is ProtectedManager
    assert type(ProtectedMembership._base_manager) is ProtectedUnscopedManager
    assert ProtectedMembership._base_manager.name == "_angee_base"
    rows = ProtectedMembership.system_queryset(lock=("self",))
    assert isinstance(rows, ProtectedQuerySet)
    assert rows.effective_actor(strict=True) == (None, True)
    assert rows.query.select_for_update
    assert rows.query.select_for_update_of == ("self",)

    def annotation(rows):
        return APIEntry.system_queryset().annotate(
            membership_label=models.Subquery(
                rows.filter(entry_id=models.OuterRef("pk")).values("label")[:1]
            )
        )

    # Building a nested native query enforces actor admission even without SQL.
    with pytest.raises(MissingActorError):
        annotation(ProtectedMembership.objects.all())
    query = annotation(ProtectedMembership.system_queryset())
    assert isinstance(query.query.annotations["membership_label"].output_field, models.CharField)
