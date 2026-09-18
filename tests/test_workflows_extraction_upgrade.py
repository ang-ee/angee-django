"""Upgrade contracts for the retired workflows_ocr persistence graph."""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.apps.registry import Apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, migrations, models, transaction
from django.db.migrations.state import ModelState, ProjectState
from django.db.migrations.writer import MigrationWriter
from rebac import ObjectRef, RelationshipTuple, SubjectRef, system_context
from rebac.models import active_relationship_model
from reversion.models import Revision, Version

from angee.base.historical_relationships import (
    delete_historical_relationships,
    ensure_historical_relationships,
)
from angee.compose.model_composition import ModelComposition
from angee.compose.runtime import Runtime
from angee.workflows_extraction.managers import _evidence_insertion
from angee.workflows_extraction.runtime_migrations import adopt_workflows_ocr as adopt
from angee.workflows_extraction.runtime_migrations import extraction_state_enums as state_enums
from angee.workflows_extraction.runtime_migrations import retire_workflows_ocr as retire
from angee.workflows_extraction.runtime_migrations import stage_workflows_extraction as stage
from angee.workflows_ocr.apps import WorkflowsOcrHistoryConfig
from tests.extraction_models import (
    EXTRACTION_MODELS,
    Extraction,
    ExtractionLineage,
)
from tests.test_agents import InferenceModel as _TestInferenceModel  # noqa: F401
from tests.workflows import workflow_table_setup

OLD_MODELS = ("Extraction", "ExtractionSource", "ExtractionPage", "ExtractionPart")
CURRENT_MODELS = (*OLD_MODELS, "ExtractionLineage")


class LegacyCopySource(models.Model):
    """Small historical row shape used to prove native SQL adoption semantics."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    payload = models.JSONField()

    class Meta:
        app_label = "tests"
        db_table = "test_workflows_ocr_upgrade_source"


class LegacyCopyTarget(models.Model):
    """Current row shape adds one derived field while retaining every old fact."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    payload = models.JSONField()
    added = models.JSONField(default=list)

    class Meta:
        app_label = "tests"
        db_table = "test_workflows_ocr_upgrade_target"


class RetainedExtractionReference(models.Model):
    """A downstream current-schema FK that must survive retained-row adoption."""

    extraction = models.ForeignKey(Extraction, on_delete=models.PROTECT)
    label = models.CharField(max_length=32)

    class Meta:
        app_label = "tests"
        db_table = "test_workflows_ocr_retained_reference"


@pytest.fixture()
def extraction_upgrade_tables(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Expose the source-suite concrete models through the migration table map."""

    current_tables = {
        model._meta.model_name: model._meta.db_table for model in EXTRACTION_MODELS if model is not ExtractionLineage
    }
    monkeypatch.setattr(adopt, "_NEW_TABLES", current_tables)
    monkeypatch.setattr(adopt, "_LINEAGE_TABLE", ExtractionLineage._meta.db_table)
    with workflow_table_setup(EXTRACTION_MODELS):
        yield


def _state(*, old: tuple[str, ...] = (), current: tuple[str, ...] = ()) -> ProjectState:
    state = ProjectState()
    for app_label, names in (("workflows_ocr", old), ("workflows_extraction", current)):
        for name in names:
            state.add_model(
                ModelState(
                    app_label=app_label,
                    name=name,
                    fields={"id": models.AutoField(primary_key=True)},
                )
            )
    return state


def _historical_extraction_apps() -> Apps:
    """Render the migration models without importing their serving managers."""

    migration_owned_apps = {
        "agents",
        "auth",
        "iam",
        "messaging",
        "reversion",
        "storage",
        "workflows_extraction",
    }
    state = ProjectState(
        real_apps={config.label for config in django_apps.get_app_configs() if config.label not in migration_owned_apps}
    )
    for app_label, model_name in (
        ("agents", "InferenceModel"),
        ("messaging", "Part"),
        ("storage", "File"),
    ):
        state.add_model(
            ModelState(
                app_label=app_label,
                name=model_name,
                fields={"id": models.BigAutoField(primary_key=True)},
            )
        )
    for app_label in ("auth", "iam", "reversion"):
        for model in django_apps.get_app_config(app_label).get_models():
            state.add_model(ModelState.from_model(model))
    for model in EXTRACTION_MODELS:
        state.add_model(ModelState.from_model(model))
    return state.apps


def test_upgrade_declarations_cover_fresh_old_only_and_complete_adoption_states() -> None:
    """Each supported graph has one unambiguous next operation."""

    fresh = _state()
    old_only = _state(old=OLD_MODELS)
    current_only = _state(current=CURRENT_MODELS)
    combined = _state(old=OLD_MODELS, current=CURRENT_MODELS)

    assert (stage.applies(fresh), adopt.applies(fresh), retire.applies(fresh)) == (False, False, False)
    assert (stage.applies(old_only), adopt.applies(old_only), retire.applies(old_only)) == (True, False, False)
    assert (stage.applies(current_only), adopt.applies(current_only), retire.applies(current_only)) == (
        False,
        False,
        False,
    )
    assert (stage.applies(combined), adopt.applies(combined), retire.applies(combined)) == (False, True, True)

    partial = _state(old=OLD_MODELS[:-1])
    with pytest.raises(ImproperlyConfigured, match="partial"):
        stage.applies(partial)
    with pytest.raises(ImproperlyConfigured, match="partial"):
        adopt.applies(partial)
    with pytest.raises(ImproperlyConfigured, match="partial"):
        retire.applies(partial)


def test_extraction_state_enum_migration_accepts_only_whole_legacy_or_current_state() -> None:
    legacy = ProjectState()
    legacy.add_model(ModelState(
        app_label="workflows_extraction",
        name="Extraction",
        fields={
            "id": models.AutoField(primary_key=True),
            "status": models.CharField(max_length=16, editable=False),
        },
    ))
    legacy.add_model(ModelState(
        app_label="workflows_extraction",
        name="ExtractionPart",
        fields={
            "id": models.AutoField(primary_key=True),
            "kind": models.CharField(max_length=32, editable=False),
        },
    ))
    assert state_enums.applies(legacy)

    current = legacy.clone()
    current.models["workflows_extraction", "extraction"].fields["status"] = state_enums.StateField(
        choices=state_enums.STATUS_CHOICES,
        editable=False,
    )
    current.models["workflows_extraction", "extractionpart"].fields["kind"] = state_enums.StateField(
        choices=state_enums.PART_KIND_CHOICES,
        editable=False,
    )
    assert not state_enums.applies(current)

    partial = current.clone()
    partial.models["workflows_extraction", "extractionpart"].fields["kind"] = models.CharField(
        choices=state_enums.PART_KIND_CHOICES,
        editable=False,
        max_length=32,
    )
    with pytest.raises(ImproperlyConfigured, match="partial vocabulary"):
        state_enums.applies(partial)


def test_legacy_selectors_preserve_only_explicit_layout_or_one_root() -> None:
    """Legacy evidence receives deterministic selectors without invented line meaning."""

    result = {
        "documents": [
            {"number": "A", "lines": [{"description": "one"}]},
            {"number": "B", "lines": []},
        ]
    }
    assert adopt._legacy_result_selectors(result, {}) == (("", ()),)
    assert adopt._legacy_result_selectors(
        result,
        {
            "document_collection": "/documents",
            "line_collection": "/lines",
        },
    ) == (
        ("/documents/0", ("/documents/0/lines/0",)),
        ("/documents/1", ()),
    )
    assert adopt._legacy_result_selectors({}, {}) == ()
    with pytest.raises(ImproperlyConfigured, match="document collection"):
        adopt._legacy_result_selectors(result, {"document_collection": "/missing"})


def test_adoption_refuses_occupied_destination_before_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mixed old/current data cannot be merged or overwrite retained identities."""

    tables = {*adopt._OLD_TABLES.values(), *adopt._NEW_TABLES.values(), adopt._LINEAGE_TABLE}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql):
            self.sql = sql

        def fetchone(self):
            return (1,)

    connection = SimpleNamespace(
        alias="default",
        cursor=lambda: Cursor(),
        introspection=SimpleNamespace(table_names=lambda cursor: sorted(tables)),
    )
    editor = SimpleNamespace(connection=connection, quote_name=lambda value: f'"{value}"')
    monkeypatch.setattr(adopt.transaction, "atomic", lambda **kwargs: nullcontext())

    with pytest.raises(ImproperlyConfigured, match="refuses mixed legacy/current data"):
        adopt.adopt_workflows_ocr(SimpleNamespace(), editor)


@pytest.mark.django_db(transaction=True)
def test_copy_preserves_primary_key_timestamps_and_json_without_double_encoding() -> None:
    """Native INSERT SELECT retains audit/JSON facts and prepares only new defaults."""

    with connection.schema_editor() as editor:
        editor.create_model(LegacyCopySource)
        editor.create_model(LegacyCopyTarget)
    try:
        source = LegacyCopySource.objects.create(payload={"invoice": {"total": "5.10"}})
        retained_created = datetime(2020, 1, 2, 3, 4, tzinfo=UTC)
        retained_updated = datetime(2021, 2, 3, 4, 5, tzinfo=UTC)
        LegacyCopySource.objects.filter(pk=source.pk).update(
            created_at=retained_created,
            updated_at=retained_updated,
        )
        with connection.schema_editor() as editor:
            adopt._copy_table(
                SimpleNamespace(get_model=lambda app_label, model_name: LegacyCopyTarget),
                editor,
                old_table=LegacyCopySource._meta.db_table,
                model_name="Extraction",
            )
        copied = LegacyCopyTarget.objects.get()
        assert copied.pk == source.pk
        assert copied.created_at == retained_created
        assert copied.updated_at == retained_updated
        assert copied.payload == {"invoice": {"total": "5.10"}}
        assert copied.added == []
    finally:
        with connection.schema_editor() as editor:
            editor.delete_model(LegacyCopyTarget)
            editor.delete_model(LegacyCopySource)


@pytest.mark.django_db(transaction=True)
def test_adoption_retains_rows_references_history_and_every_rebac_namespace_atomically(
    extraction_upgrade_tables: None,
) -> None:
    """The real adoption is complete, identity preserving, and all-or-nothing."""

    del extraction_upgrade_tables
    if connection.vendor != "postgresql":
        pytest.skip("retained extraction adoption requires PostgreSQL table identity semantics")

    quote = connection.ops.quote_name
    historical_apps = _historical_extraction_apps()
    old_tables = tuple(adopt._OLD_TABLES.values())
    target_tables = tuple(adopt._NEW_TABLES.values())
    created_reference_table = False
    old_content_type = None
    version = None
    legacy_relationships: tuple[RelationshipTuple, ...] = ()
    conflict: RelationshipTuple | None = None
    retained_pk = None
    try:
        with connection.schema_editor() as editor:
            editor.create_model(RetainedExtractionReference)
        created_reference_table = True
        with connection.cursor() as cursor:
            for old_table, target_table in zip(old_tables, target_tables, strict=True):
                cursor.execute(
                    f"CREATE TABLE {quote(old_table)} "
                    f"(LIKE {quote(target_table)} INCLUDING DEFAULTS INCLUDING GENERATED INCLUDING IDENTITY)"
                )

        source_content_type = ContentType.objects.get_for_model(ContentType)
        old_content_type = ContentType.objects.create(
            app_label="workflows_ocr",
            model="extraction",
        )
        current_content_type, _ = ContentType.objects.get_or_create(
            app_label="workflows_extraction",
            model="extraction",
        )
        revision = Revision.objects.create(date_created=datetime.now(UTC), comment="retained")
        version = Version.objects.create(
            revision=revision,
            object_id="1",
            content_type=old_content_type,
            db=connection.alias,
            format="json",
            serialized_data=json.dumps(
                [
                    {
                        "model": "workflows_ocr.extraction",
                        "pk": "1",
                        "fields": {"status": "succeeded"},
                    }
                ]
            ),
            object_repr="retained extraction",
        )

        with (
            transaction.atomic(using=connection.alias),
            _evidence_insertion.scope(connection.alias, None),
            system_context(reason="test retained extraction upgrade"),
        ):
            retained = Extraction.system_objects.create(
                revision=1,
                lineage_key="retained-upgrade-lineage",
                reuse_key="retained-upgrade-reuse",
                status="succeeded",
                error_code="",
                schema_id="invoice-v1",
                schema_digest="a" * 64,
                schema={"type": "object"},
                engine="none",
                engine_config={"evidence_layout": {"root_document_on_missing": True}},
                result={"invoice": {"total": "5.10"}},
                provenance={"retained": True},
                object_id="retained-source",
                content_type=source_content_type,
            )
        retained_pk = retained.pk
        version.object_id = str(retained_pk)
        version.save(update_fields=["object_id"])
        reference = RetainedExtractionReference.objects.create(
            extraction=retained,
            label="downstream",
        )

        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {quote(adopt._OLD_TABLES['extraction'])} "
                f"SELECT * FROM {quote(adopt._NEW_TABLES['extraction'])}"
            )

        old_types = tuple(adopt._RESOURCE_TYPES)
        new_types = tuple(adopt._RESOURCE_TYPES.values())
        legacy_relationships = tuple(
            RelationshipTuple(
                resource=ObjectRef(old_type, f"resource-{index}"),
                relation="reader",
                subject=SubjectRef.of(old_types[(index + 1) % len(old_types)], f"subject-{index}"),
                caveat_name="regional" if index == len(old_types) - 1 else "",
                caveat_context={"region": "eu"} if index == len(old_types) - 1 else {},
            )
            for index, old_type in enumerate(old_types)
        )
        conflict = RelationshipTuple(
            resource=ObjectRef(new_types[-1], "resource-3"),
            relation="reader",
            subject=SubjectRef.of(new_types[0], "subject-3"),
            caveat_name="regional",
            caveat_context={"region": "us"},
        )
        ensure_historical_relationships(
            django_apps,
            using=connection.alias,
            relationships=(*legacy_relationships, conflict),
        )

        with transaction.atomic(using=connection.alias):
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")
                cursor.execute(
                    f"DELETE FROM {quote(adopt._NEW_TABLES['extraction'])} WHERE id = %s",
                    [retained_pk],
                )
            with connection.schema_editor() as editor:
                with pytest.raises(ImproperlyConfigured, match="conflicting grant facts"):
                    adopt.adopt_workflows_ocr(historical_apps, editor)

            assert not Extraction.system_objects.filter(pk=retained_pk).exists()
            version.refresh_from_db()
            assert version.content_type_id == old_content_type.pk
            assert "workflows_ocr.extraction" in version.serialized_data
            relationship_model = active_relationship_model()
            if relationship_model._meta.object_name == "RelationshipRegistry":
                retained_old = relationship_model._base_manager.filter(
                    resource_fk__resource_type__in=old_types,
                    resource_fk__resource_id__startswith="resource-",
                )
            else:
                retained_old = relationship_model._base_manager.filter(
                    resource_type__in=old_types,
                    resource_id__startswith="resource-",
                )
            assert retained_old.count() == len(old_types)

            delete_historical_relationships(
                django_apps,
                using=connection.alias,
                relationships=(conflict,),
            )
            with connection.schema_editor() as editor:
                adopt.adopt_workflows_ocr(historical_apps, editor)

        copied = Extraction.system_objects.get(pk=retained_pk)
        assert copied.result == {"invoice": {"total": "5.10"}}
        assert copied.provenance == {"retained": True}
        assert copied.document_map[0]["selector"] == ""
        with system_context(reason="verify retained extraction lineage"):
            lineage = ExtractionLineage.objects.get(key="retained-upgrade-lineage")
        assert lineage.head_id == retained_pk
        reference.refresh_from_db()
        assert reference.extraction_id == retained_pk
        assert reference.extraction.pk == retained_pk

        version.refresh_from_db()
        assert version.content_type_id == current_content_type.pk
        assert "workflows_extraction.extraction" in version.serialized_data
        assert not ContentType.objects.filter(pk=old_content_type.pk).exists()

        relationship_model = active_relationship_model()
        if relationship_model._meta.object_name == "RelationshipRegistry":
            resource_axes = set(
                relationship_model._base_manager.filter(
                    resource_fk__resource_id__startswith="resource-",
                ).values_list("resource_fk__resource_type", flat=True)
            )
            subject_axes = set(
                relationship_model._base_manager.filter(
                    subject_fk__resource_id__startswith="subject-",
                ).values_list("subject_fk__resource_type", flat=True)
            )
        else:
            resource_axes = set(
                relationship_model._base_manager.filter(
                    resource_id__startswith="resource-",
                ).values_list("resource_type", flat=True)
            )
            subject_axes = set(
                relationship_model._base_manager.filter(
                    subject_id__startswith="subject-",
                ).values_list("subject_type", flat=True)
            )
        assert resource_axes == set(new_types)
        assert subject_axes == set(new_types)
    finally:
        if conflict is not None:
            delete_historical_relationships(
                django_apps,
                using=connection.alias,
                relationships=(conflict,),
            )
        if legacy_relationships:
            current_relationships = tuple(
                RelationshipTuple(
                    resource=ObjectRef(adopt._RESOURCE_TYPES[row.resource.resource_type], row.resource.resource_id),
                    relation=row.relation,
                    subject=SubjectRef.of(
                        adopt._RESOURCE_TYPES[row.subject.subject_type],
                        row.subject.subject_id,
                        row.subject.optional_relation,
                    ),
                    caveat_name=row.caveat_name,
                    caveat_context=row.caveat_context,
                    expires_at=row.expires_at,
                )
                for row in legacy_relationships
            )
            delete_historical_relationships(
                django_apps,
                using=connection.alias,
                relationships=(*legacy_relationships, *current_relationships),
            )
        if created_reference_table:
            with connection.schema_editor() as editor:
                editor.delete_model(RetainedExtractionReference)
        with connection.cursor() as cursor:
            for table in reversed(old_tables):
                cursor.execute(f"DROP TABLE IF EXISTS {quote(table)} CASCADE")


def test_history_only_writer_stays_in_composer_runtime(tmp_path, monkeypatch, settings) -> None:
    """Django appends retained history but authors new nodes under generated runtime."""

    source_module = importlib.import_module("angee.workflows_ocr")
    config = WorkflowsOcrHistoryConfig("angee.workflows_ocr", source_module)
    runtime_module = f"upgrade_runtime_{tmp_path.name.replace('-', '_')}"
    runtime_dir = tmp_path / runtime_module
    runtime = Runtime(
        (config,),
        ModelComposition({}, {}),
        runtime_dir=runtime_dir,
        runtime_module=runtime_module,
    )
    for relative, source in runtime.render_sources().items():
        path = runtime_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(settings, "MIGRATION_MODULES", {}, raising=False)
    for name in tuple(sys.modules):
        if name == runtime_module or name.startswith(f"{runtime_module}."):
            monkeypatch.delitem(sys.modules, name)
    importlib.invalidate_caches()
    runtime.configure_migration_modules()

    writer = MigrationWriter(migrations.Migration("0002_probe", "workflows_ocr"))
    retained = importlib.import_module(f"{runtime_module}.workflows_ocr.migrations")
    assert settings.MIGRATION_MODULES["workflows_ocr"] == f"{runtime_module}.workflows_ocr.migrations"
    assert writer.basedir == str(runtime_dir / "workflows_ocr" / "migrations")
    source_migrations = importlib.import_module("angee.workflows_ocr.migrations")
    assert str(source_migrations.__path__[0]) in tuple(retained.__path__)
