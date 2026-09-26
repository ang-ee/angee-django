"""Focused checks for migration-safe historical REBAC relationship writes."""

from __future__ import annotations

from datetime import UTC, datetime
from inspect import Parameter, signature
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, migrations, models
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from rebac import ObjectRef, RelationshipTuple, SubjectRef

from angee.base.historical_relationships import (
    delete_historical_relationships,
    ensure_historical_relationships,
    retarget_historical_resource,
)


def _historical_state():
    return MigrationExecutor(connection).loader.project_state([("rebac", "0003_schema_relation_backing")])


def _tuple(resource_id: str, *, caveat_name: str = "", expires_at=None):
    return RelationshipTuple(
        resource=ObjectRef("example/document", resource_id),
        relation="reader",
        subject=SubjectRef.of("auth/user", "reader-1"),
        caveat_name=caveat_name,
        caveat_context={"region": "eu"} if caveat_name else {},
        expires_at=expires_at,
    )


@pytest.mark.parametrize(
    ("helper", "parameters"),
    [
        (ensure_historical_relationships, ("apps", "using", "relationships")),
        (delete_historical_relationships, ("apps", "using", "relationships")),
        (retarget_historical_resource, ("apps", "using", "old", "new")),
    ],
)
def test_historical_helpers_keep_released_migration_signatures(helper, parameters) -> None:
    """Carried-forward RunPython callers supply Django's migration connection."""

    actual = signature(helper).parameters
    assert tuple(actual) == parameters
    assert actual["apps"].kind is Parameter.POSITIONAL_OR_KEYWORD
    for name in parameters[1:]:
        assert actual[name].kind is Parameter.KEYWORD_ONLY
        assert actual[name].default is Parameter.empty


class HistoricalRelationshipTests(TransactionTestCase):
    """The helper writes exact historical tuples without a live manager."""

    def test_resource_retarget_preserves_exact_grants_and_registry_identity(self) -> None:
        """Both stores merge identical grants or retain the old FK row in place."""

        expires_at = datetime(2030, 1, 1, tzinfo=UTC)
        for storage in ("denormalized", "registry"):
            for target_exists in (False, True):
                with self.subTest(storage=storage, target_exists=target_exists):
                    old = ObjectRef("example/document", f"{storage}-{target_exists}-old")
                    new = ObjectRef("example/document", f"{storage}-{target_exists}-new")
                    original = RelationshipTuple(
                        resource=old,
                        relation="reader",
                        subject=SubjectRef.of("auth/user", "reader-1"),
                        caveat_name="regional",
                        caveat_context={"region": "eu"},
                        expires_at=expires_at,
                    )
                    replacement = RelationshipTuple(
                        resource=new,
                        relation=original.relation,
                        subject=original.subject,
                        caveat_name=original.caveat_name,
                        caveat_context=original.caveat_context,
                        expires_at=original.expires_at,
                    )
                    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
                        apps = _historical_state().apps
                        ensure_historical_relationships(apps, using=connection.alias, relationships=(original,))
                        if target_exists:
                            ensure_historical_relationships(apps, using=connection.alias, relationships=(replacement,))
                        old_registry_pk = None
                        if storage == "registry":
                            resources = apps.get_model("rebac", "RebacResource")._base_manager
                            old_registry_pk = resources.get(
                                resource_type=old.resource_type,
                                resource_id=old.resource_id,
                            ).pk
                        retarget_historical_resource(apps, using=connection.alias, old=old, new=new)
                        retarget_historical_resource(apps, using=connection.alias, old=old, new=new)
                        model_name = "RelationshipRegistry" if storage == "registry" else "Relationship"
                        rows = apps.get_model("rebac", model_name)._base_manager
                        if storage == "registry":
                            rows = rows.filter(
                                resource_fk__resource_type=new.resource_type,
                                resource_fk__resource_id=new.resource_id,
                            )
                            self.assertFalse(
                                resources.filter(
                                    resource_type=old.resource_type,
                                    resource_id=old.resource_id,
                                ).exists()
                            )
                            if not target_exists:
                                self.assertEqual(
                                    resources.get(
                                        resource_type=new.resource_type,
                                        resource_id=new.resource_id,
                                    ).pk,
                                    old_registry_pk,
                                )
                        else:
                            rows = rows.filter(
                                resource_type=new.resource_type,
                                resource_id=new.resource_id,
                            )
                        self.assertEqual(rows.count(), 1)
                        retained = rows.get()
                        self.assertEqual(retained.caveat_context, {"region": "eu"})
                        self.assertEqual(retained.expires_at, expires_at)

    def test_resource_retarget_rejects_conflicting_grants(self) -> None:
        """A target with changed caveat facts cannot consume the old grant."""

        for storage in ("denormalized", "registry"):
            with self.subTest(storage=storage):
                old = ObjectRef("example/document", f"{storage}-conflict-old")
                new = ObjectRef("example/document", f"{storage}-conflict-new")
                grants = (
                    RelationshipTuple(
                        resource=old,
                        relation="reader",
                        subject=SubjectRef.of("auth/user", "reader-1"),
                        caveat_name="regional",
                        caveat_context={"region": "eu"},
                    ),
                    RelationshipTuple(
                        resource=new,
                        relation="reader",
                        subject=SubjectRef.of("auth/user", "reader-1"),
                        caveat_name="regional",
                        caveat_context={"region": "us"},
                    ),
                )
                with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
                    apps = _historical_state().apps
                    ensure_historical_relationships(apps, using=connection.alias, relationships=grants)
                    with self.assertRaisesRegex(ImproperlyConfigured, "conflicting grant facts"):
                        retarget_historical_resource(apps, using=connection.alias, old=old, new=new)
                    model_name = "RelationshipRegistry" if storage == "registry" else "Relationship"
                    rows = apps.get_model("rebac", model_name)._base_manager
                    for resource in (old, new):
                        lookup = (
                            {
                                "resource_fk__resource_type": resource.resource_type,
                                "resource_fk__resource_id": resource.resource_id,
                            }
                            if storage == "registry"
                            else {
                                "resource_type": resource.resource_type,
                                "resource_id": resource.resource_id,
                            }
                        )
                        self.assertEqual(rows.filter(**lookup).count(), 1)

    def test_add_idempotency_and_exact_reverse_in_both_storage_shapes(self) -> None:
        for storage in ("denormalized", "registry"):
            with self.subTest(storage=storage):
                apps = _historical_state().apps
                requested = _tuple(f"{storage}-requested")
                retained = _tuple(
                    f"{storage}-retained",
                    caveat_name="regional",
                    expires_at=datetime(2030, 1, 1, tzinfo=UTC),
                )
                model_name = "RelationshipRegistry" if storage == "registry" else "Relationship"

                with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
                    ensure_historical_relationships(
                        apps,
                        using=connection.alias,
                        relationships=(requested, retained),
                    )
                    ensure_historical_relationships(
                        apps,
                        using=connection.alias,
                        relationships=(requested,),
                    )

                    rows = apps.get_model("rebac", model_name)._base_manager
                    resource_ids = (
                        requested.resource.resource_id,
                        retained.resource.resource_id,
                    )
                    if storage == "registry":
                        rows = rows.filter(
                            resource_fk__resource_type="example/document",
                            resource_fk__resource_id__in=resource_ids,
                        )
                    else:
                        rows = rows.filter(
                            resource_type="example/document",
                            resource_id__in=resource_ids,
                        )
                    self.assertEqual(rows.count(), 2)

                    delete_historical_relationships(
                        apps,
                        using=connection.alias,
                        relationships=(requested,),
                    )
                    self.assertEqual(rows.count(), 1)
                    remaining = rows.get()
                    self.assertEqual(remaining.relation, "reader")
                    self.assertEqual(remaining.caveat_name, "regional")
                    self.assertEqual(remaining.caveat_context, {"region": "eu"})
                    self.assertEqual(remaining.expires_at, retained.expires_at)

                    if storage == "registry":
                        resources = apps.get_model("rebac", "RebacResource")._base_manager
                        self.assertEqual(
                            resources.filter(
                                resource_type="example/document",
                                resource_id__in=resource_ids,
                            ).count(),
                            2,
                        )

    @override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
    def test_conflicts_and_unknown_historical_shapes_fail_closed(self) -> None:
        state = _historical_state()
        apps = state.apps
        requested = _tuple("conflict")
        relationship = apps.get_model("rebac", "RelationshipRegistry")
        resource = apps.get_model("rebac", "RebacResource")
        resources = resource._base_manager
        resource_row = resources.create(
            resource_type=requested.resource.resource_type,
            resource_id=requested.resource.resource_id,
        )
        subject_row = resources.create(
            resource_type=requested.subject.subject_type,
            resource_id=requested.subject.subject_id,
        )
        relationship._base_manager.create(
            resource_fk_id=resource_row.pk,
            subject_fk_id=subject_row.pk,
            relation=requested.relation,
            optional_subject_relation=requested.subject.optional_relation,
            caveat_name=requested.caveat_name,
            caveat_context={"unexpected": True},
        )

        with self.assertRaisesRegex(ImproperlyConfigured, "conflicting exact facts"):
            ensure_historical_relationships(
                apps,
                using=connection.alias,
                relationships=(requested,),
            )

        malformed_fields = state.clone()
        malformed_fields.remove_field("rebac", "relationshipregistry", "written_at_xid")
        with self.assertRaisesRegex(ImproperlyConfigured, "storage shape is unsupported"):
            ensure_historical_relationships(
                malformed_fields.apps,
                using=connection.alias,
                relationships=(requested,),
            )

        duplicate_prone = state.clone()
        migrations.RemoveConstraint(
            model_name="rebacresource",
            name="rebac_resource_uniq",
        ).state_forwards("rebac", duplicate_prone)
        with self.assertRaisesRegex(
            ImproperlyConfigured,
            "lacks its exact identity constraint",
        ):
            ensure_historical_relationships(
                duplicate_prone.apps,
                using=connection.alias,
                relationships=(requested,),
            )

    def test_historical_retarget_locks_rows_inside_its_transaction(self) -> None:
        """Both stores retain transaction and lock semantics for exact grant merges."""

        apps = _historical_state().apps
        resource = apps.get_model("rebac", "RebacResource")
        for storage in ("denormalized", "registry"):
            with (
                self.subTest(storage=storage),
                override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage),
            ):
                requested = _tuple(f"{storage}-locked")
                replacement = _tuple(f"{storage}-retargeted")
                model_name = "RelationshipRegistry" if storage == "registry" else "Relationship"
                relationship = apps.get_model("rebac", model_name)
                ensure_historical_relationships(
                    apps,
                    using=connection.alias,
                    relationships=(requested, replacement),
                )
                rows = relationship._base_manager
                resource_lookup = "resource_fk__resource_id" if storage == "registry" else "resource_id"
                self.assertEqual(rows.filter(**{resource_lookup: requested.resource.resource_id}).count(), 1)
                if storage == "registry":
                    self.assertEqual(
                        resource._base_manager
                        .filter(
                            resource_type=requested.resource.resource_type,
                            resource_id=requested.resource.resource_id,
                        )
                        .count(),
                        1,
                    )

                locked_models = set()
                native_select_for_update = models.QuerySet.select_for_update

                def select_for_update(queryset, *args, **kwargs):
                    self.assertTrue(connection.in_atomic_block)
                    locked_models.add(queryset.model)
                    return native_select_for_update(queryset, *args, **kwargs)

                with patch.object(models.QuerySet, "select_for_update", select_for_update):
                    retarget_historical_resource(
                        apps,
                        using=connection.alias,
                        old=requested.resource,
                        new=replacement.resource,
                    )
                expected_locked_models = {relationship, resource} if storage == "registry" else {relationship}
                self.assertEqual(locked_models, expected_locked_models)
                self.assertFalse(rows.filter(**{resource_lookup: requested.resource.resource_id}).exists())
                retained_rows = rows.filter(**{resource_lookup: replacement.resource.resource_id})
                self.assertEqual(retained_rows.count(), 1)
                delete_historical_relationships(
                    apps,
                    using=connection.alias,
                    relationships=(replacement,),
                )
                self.assertFalse(retained_rows.exists())
