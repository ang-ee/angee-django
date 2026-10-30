"""Startup declarations and request policy use their separate native authorities."""

from pathlib import Path
from types import ModuleType, SimpleNamespace

from django.apps import AppConfig
from django.db import models
from django.test.utils import isolate_apps
from rebac.mixins import RebacModelBase
from rebac.schema import parse_zed

from angee.base.permissions import (
    bind_rebac_schema,
    declared_schema_path,
    effective_rebac_definition,
    effective_rebac_schema,
)
from angee.graphql.access import is_gated_read_axis


@isolate_apps()
def test_requests_use_synced_policy_without_reading_artifacts(monkeypatch):
    class Record(models.Model, metaclass=RebacModelBase):
        secret = models.CharField(max_length=30)
        class Meta:
            abstract = True
            app_label = 'policy_probe'
            rebac_resource_type = 'probe/record'

    schema = parse_zed(
        'definition probe/record {\npermission read = anonymous\npermission read__secret = anonymous\n}'
    )
    monkeypatch.setattr('angee.base.permissions.backend', lambda: SimpleNamespace(schema=lambda: schema))
    def forbid_read(*args, **kwargs):
        raise AssertionError('request read permission artifacts')
    monkeypatch.setattr(Path, 'read_text', forbid_read)
    assert effective_rebac_definition(Record) is schema.get_definition('probe/record')
    assert is_gated_read_axis(Record, 'secret')
    declared = parse_zed('definition probe/record { permission read = anonymous }')
    assert not is_gated_read_axis(
        Record, 'secret', definition_for=lambda model: declared.get_definition('probe/record'),
    )


def test_startup_snapshot_needs_no_policy_backend(tmp_path, monkeypatch):
    path = tmp_path / 'permissions.zed'
    path.write_text('definition probe/record { permission read = anonymous }')
    config = SimpleNamespace(path=str(tmp_path), rebac_schema=str(path))
    def forbid_backend():
        raise AssertionError('startup queried policy backend')
    monkeypatch.setattr('angee.base.permissions.backend', forbid_backend)
    assert effective_rebac_schema(config).get_definition('probe/record') is not None


def test_schema_binding_retains_source_and_honors_a_new_authored_override(tmp_path):
    class ProbeConfig(AppConfig):
        name = "probe"
        path = str(tmp_path)

    config = ProbeConfig("probe", ModuleType("probe"))
    source = tmp_path / "permissions.zed"
    source.write_text("definition probe/source {}")
    generated = tmp_path / "compiled.zed"
    generated.write_text("definition probe/compiled {}")
    override = tmp_path / "override.zed"
    override.write_text("definition probe/override {}")
    assert declared_schema_path(config) == source
    bind_rebac_schema(config, generated)
    assert declared_schema_path(config) == source
    assert effective_rebac_schema(config).get_definition("probe/compiled") is not None
    setattr(config, "rebac_schema", override)
    assert declared_schema_path(config) == override
    bind_rebac_schema(config, generated)
    assert declared_schema_path(config) == override
