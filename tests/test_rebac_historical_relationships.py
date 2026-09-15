"""Focused checks for migration-safe historical REBAC relationship writes."""

from __future__ import annotations

from datetime import UTC, datetime

from django.core.exceptions import ImproperlyConfigured
from django.db import connection, connections, migrations
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from rebac import ObjectRef, RelationshipTuple, SubjectRef

from angee.base.historical_relationships import (
    delete_historical_relationships,
    ensure_historical_relationships,
)


def _historical_state():
    return MigrationExecutor(connection).loader.project_state(
        [("rebac", "0003_schema_relation_backing")]
    )


def _tuple(resource_id: str, *, caveat_name: str = "", expires_at=None):
    return RelationshipTuple(
        resource=ObjectRef("example/document", resource_id),
        relation="reader",
        subject=SubjectRef.of("auth/user", "reader-1"),
        caveat_name=caveat_name,
        caveat_context={"region": "eu"} if caveat_name else {},
        expires_at=expires_at,
    )


class HistoricalRelationshipTests(TransactionTestCase):
    """The helper writes exact historical tuples without a live manager."""

    databases = {"default", "historical_relationships_other"}

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
                model_name = (
                    "RelationshipRegistry" if storage == "registry" else "Relationship"
                )

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

                    rows = apps.get_model("rebac", model_name)._base_manager.using(
                        connection.alias
                    )
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
                        resources = apps.get_model(
                            "rebac", "RebacResource"
                        )._base_manager.using(connection.alias)
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
        resources = resource._base_manager.using(connection.alias)
        resource_row = resources.create(
            resource_type=requested.resource.resource_type,
            resource_id=requested.resource.resource_id,
        )
        subject_row = resources.create(
            resource_type=requested.subject.subject_type,
            resource_id=requested.subject.subject_id,
        )
        relationship._base_manager.using(connection.alias).create(
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

    @override_settings(REBAC_LOCAL_BACKEND_STORAGE="registry")
    def test_every_historical_write_binds_the_supplied_alias(self) -> None:
        apps = _historical_state().apps
        relationship = apps.get_model("rebac", "RelationshipRegistry")
        resource = apps.get_model("rebac", "RebacResource")
        requested = _tuple("alias-bound")
        other_alias = "historical_relationships_other"

        def reject_default_sql(execute, sql, params, many, context):
            raise AssertionError(f"historical relationship helper used default DB: {sql}")

        other = connections[other_alias]
        self.assertIsNot(other, connection)
        with connection.execute_wrapper(reject_default_sql):
            ensure_historical_relationships(
                apps,
                using=other_alias,
                relationships=(requested,),
            )
            rows = relationship._base_manager.using(other_alias).filter(
                resource_fk__resource_type=requested.resource.resource_type,
                resource_fk__resource_id=requested.resource.resource_id,
                relation=requested.relation,
                subject_fk__resource_type=requested.subject.subject_type,
                subject_fk__resource_id=requested.subject.subject_id,
            )
            self.assertEqual(rows.count(), 1)
            self.assertEqual(
                resource._base_manager.using(other_alias).filter(
                    resource_type=requested.resource.resource_type,
                    resource_id=requested.resource.resource_id,
                ).count(),
                1,
            )
            delete_historical_relationships(
                apps,
                using=other_alias,
                relationships=(requested,),
            )
            self.assertFalse(rows.exists())
