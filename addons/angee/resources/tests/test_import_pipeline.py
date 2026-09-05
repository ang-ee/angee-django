"""Native datasets preserve import identity, atomicity and diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import tablib
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, models, router, transaction
from rebac.models import active_relationship_model

from angee.base.models import AngeeModel
from angee.resources.entries import ResourceEntry
from angee.resources.exceptions import ResourceLoadError
from angee.resources.models import Resource
from angee.resources.tests.test_resources import addon, entry


@pytest.mark.parametrize(("suffix", "separator"), [("csv", ","), ("tsv", "\t")])
def test_rectangular_sources_reuse_the_loaded_dataset(
    tmp_path: Path,
    monkeypatch: Any,
    suffix: str,
    separator: str,
) -> None:
    """Parsing retains one native data object instead of materializing DTO rows."""

    path = tmp_path / f"input.{suffix}"
    path.write_text(
        separator.join(["title", "xref"])
        + "\n"
        + "\n".join(separator.join([f"Title {i}", f" item-{i} "]) for i in range(1000)),
    )
    source = entry(tmp_path, {"path": path.name, "model": "base.UnresolvedSourceModel"})
    parsed = []
    read = ResourceEntry._read_tabular

    def capture(owner: Any, *args: Any) -> tablib.Dataset:
        dataset = read(owner, *args)
        parsed.append(dataset)
        return dataset

    monkeypatch.setattr(ResourceEntry, "_read_tabular", capture)
    (group,) = source.read_groups()
    assert group.dataset is parsed[0]
    assert group.dataset.headers == ["_xref", "title"]
    assert group.dataset[0] == ("item-0", "Title 0")
    assert group.dataset[-1] == ("item-999", "Title 999")
    assert group.source_rows == list(range(1, 1001))
    assert source.read_groups()[0] is group
    assert len(parsed) == 1


def test_mixed_sources_keep_sparse_cells_and_original_row_indexes(tmp_path: Path) -> None:
    """Envelope model precedence and native rectangular nulls remain explicit."""

    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps(
            {
                "_meta": {"model": "base.First"},
                "rows": [
                    {"_xref": " first ", "fields": {"title": "one"}},
                    {"model": "base.Second", "xref": "second", "fields": {"count": 2}},
                    {"_xref": "third", "fields": {"model": "real field", "enabled": True}},
                ],
            }
        )
    )
    first, second = entry(tmp_path, {"path": path.name}).read_groups()
    assert first.model_label == "base.First"
    assert second.model_label == "base.Second"
    assert first.source_rows == [1, 3]
    assert second.source_rows == [2]
    assert first.dataset.headers == ["_xref", "title", "model", "enabled"]
    assert first.dataset.dict == [
        {"_xref": "first", "title": "one", "model": None, "enabled": None},
        {"_xref": "third", "title": None, "model": "real field", "enabled": True},
    ]


@pytest.mark.django_db(transaction=True)
def test_native_import_pipeline_rolls_back_all_groups_grants_and_hooks(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Rows, M2M, ledger and grants share one commit; real model fields hash."""

    events = []

    class PipelineTag(AngeeModel):
        name = models.CharField(max_length=30)

        class Meta:
            app_label = "base"

    class PipelineItem(AngeeModel):
        model = models.CharField(max_length=24)
        tags = models.ManyToManyField(PipelineTag)

        @classmethod
        def after_resource_load(cls, instances: Any, **kwargs: Any) -> None:
            events.append("hook")
            transaction.on_commit(lambda: events.append("committed"))

        class Meta:
            app_label = "base"

    class PipelineLedger(Resource):
        class Meta(Resource.Meta):
            app_label = "base"
            abstract = False

    user_model = get_user_model()
    rows = [
        {"model": "base.PipelineTag", "_xref": "tag", "fields": {"name": "tag"}},
        {"model": user_model._meta.label, "_xref": "user", "fields": {"username": "pipeline-user", "password": "!"}},
        {
            "model": "base.PipelineItem",
            "_xref": "item",
            "fields": {"model": "v1", "tags": ["resource_addon.tag"]},
        },
    ]
    grant = {"resource": "angee/role:admin", "relation": "member", "subject": "resource_addon.user"}
    data_path = tmp_path / "data.json"
    grant_path = tmp_path / "grants.json"
    data_path.write_text(json.dumps({"rows": rows}))
    grant_path.write_text(json.dumps([grant]))
    owner = addon(
        tmp_path,
        manifest={
            "master": (
                {"path": data_path.name},
                {"path": grant_path.name, "kind": "grants", "depends_on": [data_path.name]},
            ),
            "install": (),
            "demo": (),
        },
    )
    models_to_create = (PipelineTag, PipelineItem, PipelineLedger)
    with connection.schema_editor() as editor:
        for model in models_to_create:
            editor.create_model(model)
    call_command("rebac", "sync", verbosity=0)
    relationships = active_relationship_model()._default_manager
    relationship_count = relationships.count()

    def assert_rolled_back() -> None:
        assert PipelineTag._base_manager.count() == 0
        assert PipelineItem._base_manager.count() == 0
        assert PipelineItem.tags.through.objects.count() == 0
        assert PipelineLedger.objects.count() == 0
        assert not user_model._base_manager.filter(username="pipeline-user").exists()
        assert relationships.count() == relationship_count
        assert "committed" not in events

    try:
        validated = PipelineLedger.objects.validate_addons((owner,), tiers=["master"])
        assert validated.checked_files == 2
        assert validated.checked_rows == 4
        assert events == []
        assert_rolled_back()
        dry = PipelineLedger.objects.load_addons((owner,), tiers=["master"], dry_run=True)
        assert dry.created == 4
        assert events == []
        assert_rolled_back()

        grant_path.write_text(json.dumps([{**grant, "subject": "resource_addon.missing"}]))
        with pytest.raises(ResourceLoadError, match="unresolved xref"):
            PipelineLedger.objects.load_addons((owner,), tiers=["master"])
        assert events == ["hook"]
        assert_rolled_back()
        events.clear()

        grant_path.write_text(json.dumps([grant]))
        rows[2]["fields"]["model"] = "invalid" * 10
        data_path.write_text(json.dumps({"rows": rows}))
        with pytest.raises(ResourceLoadError, match=r"data.json: 3:"):
            PipelineLedger.objects.load_addons((owner,), tiers=["master"])
        assert events == []
        assert_rolled_back()

        rows[2]["fields"]["model"] = "v1"
        data_path.write_text(json.dumps({"rows": rows}))
        loaded = PipelineLedger.objects.load_addons((owner,), tiers=["master"])
        assert loaded.created == 4
        assert PipelineLedger.objects.count() == 3
        assert PipelineItem.tags.through.objects.count() == 1
        assert relationships.count() == relationship_count + 1
        assert events == ["hook", "committed"]

        rows[2]["fields"]["model"] = "v2"
        data_path.write_text(json.dumps({"rows": rows}))
        changed = PipelineLedger.objects.load_addons((owner,), tiers=["master"])
        assert changed.updated == 1
        assert changed.skipped == 3
        assert PipelineItem._base_manager.get().model == "v2"
        unchanged = PipelineLedger.objects.load_addons((owner,), tiers=["master"])
        assert unchanged.loaded == 0
        assert unchanged.skipped == 4

        route = router.db_for_write
        with monkeypatch.context() as patch:
            patch.setattr(
                router,
                "db_for_write",
                lambda model, **hints: "other" if model is PipelineItem else route(model, **hints),
            )
            with pytest.raises(ResourceLoadError, match="default database"):
                PipelineLedger.objects.load_addons((owner,), tiers=["master"])
        assert PipelineItem._base_manager.get().model == "v2"
    finally:
        with connection.schema_editor() as editor:
            for model in reversed(models_to_create):
                editor.delete_model(model)
