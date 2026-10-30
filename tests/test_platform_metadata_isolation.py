"""Data-only catalogue boundaries and explicit local wheel admission."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from rebac import system_context

from angee.integrate.credentials import CredentialKind
from angee.integrate.models import Credential
from angee.operator.daemon import OperatorDaemonError
from angee.platform_integrate_vcs import catalog
from angee.platform_integrate_vcs.metadata_worker import MAX_OUTPUT_BYTES
from tests.conftest import Addon, Repository, Source, VcsBridge, make_integration


def wheel_files():
    record = io.StringIO()
    files = {
        "local_addon-1.0.dist-info/METADATA": (
            "Metadata-Version: 2.4\nName: local-addon\nVersion: 1.0\nSummary: Built locally\n"
        ),
        "local_addon-1.0.dist-info/entry_points.txt": "[angee.addons]\nlocal.addon = local.addon\n",
        "local/addon/pyproject.toml": '[tool.angee.platform]\ncategory="Integration"\n',
    }
    csv.writer(record).writerows((name, "", "") for name in files)
    files["local_addon-1.0.dist-info/RECORD"] = record.getvalue()
    return files


def snapshot(files=None, path="addons/local"):
    return json.dumps({"schema": 1, "packages": [{"path": path, "files": wheel_files() if files is None else files}]})


@pytest.mark.parametrize("name", ["../escape.py", "/escape.py", "addon/execute.py", "addon\\pyproject.toml"])
def test_job_metadata_cannot_write_executable_or_escaping_files(name):
    files = {**wheel_files(), name: "raise AssertionError('must never execute')"}
    with pytest.raises(ImproperlyConfigured, match="Catalogue metadata was invalid"):
        catalog.descriptors_from_metadata(snapshot(files))


def test_native_record_cannot_escape_the_metadata_snapshot():
    files = wheel_files()
    files["local_addon-1.0.dist-info/RECORD"] += "../../outside/pyproject.toml,,\n"
    with pytest.raises(ImproperlyConfigured, match="normalized relative"):
        catalog.descriptors_from_metadata(snapshot(files))


def test_job_metadata_must_stay_inside_selected_source_scope():
    with pytest.raises(ImproperlyConfigured, match="selected source scope"):
        catalog.descriptors_from_metadata(snapshot(path="elsewhere"), scope="addons")


def test_job_metadata_is_bounded_before_json_parsing():
    with pytest.raises(ImproperlyConfigured, match="byte limit"):
        catalog.descriptors_from_metadata(" " * (MAX_OUTPUT_BYTES + 1))


@pytest.mark.parametrize("status", ["failed", "blocked", "unknown"])
def test_failed_or_invalid_operations_never_become_empty_catalogues(status):
    with pytest.raises(OperatorDaemonError, match="preparation failed"):
        catalog.metadata_output(SimpleNamespace(), {"id": "operation", "status": status, "error": "build unavailable"})


def test_native_operation_receipt_is_polled_before_reading_metadata(monkeypatch):
    monkeypatch.setattr(catalog.time, "sleep", lambda _: None)
    identifiers = []
    def job_run(identifier):
        identifiers.append(identifier)
        return {"id": identifier, "status": "succeeded", "output": snapshot()}
    assert catalog.metadata_output(SimpleNamespace(job_run=job_run), {
        "id": "operation", "status": "pending",
    }) == snapshot()
    assert identifiers == ["operation"]


@pytest.mark.parametrize("kind,material,expected", [
    (CredentialKind.OAUTH, {"access_token": "oauth-token"}, {"username": "provider", "password": "oauth-token"}),
    (CredentialKind.STATIC_TOKEN, {"api_key": "static-token"}, {"username": "provider", "password": "static-token"}),
    (CredentialKind.BASIC_AUTH, {"username": "alice", "password": "secret"},
     {"username": "alice", "password": "secret"}),
])
def test_git_auth_material_belongs_to_credential_kind(kind, material, expected):
    credential = SimpleNamespace(material=json.dumps(material))
    assert kind.handler.git_credentials(credential, token_username="provider") == expected


def test_credential_refresh_precedes_handler_material_lookup():
    calls = []
    owner = SimpleNamespace(ensure_fresh=lambda: calls.append("refresh"))
    def material(credential, *, token_username):
        assert credential is owner and calls == ["refresh"]
        calls.append("material")
        return {"username": token_username, "password": "fresh-token"}
    owner.handler = SimpleNamespace(git_credentials=material)
    assert Credential.git_credentials(owner, token_username="provider")["password"] == "fresh-token"
    assert calls == ["refresh", "material"]


def test_unsupported_git_credential_does_not_reveal_secret():
    with pytest.raises(ValueError, match="HTTPS Git"):
        CredentialKind.SSH_KEY.handler.git_credentials(SimpleNamespace(), token_username="provider")


def local_source(root):
    bridge = make_integration(
        "local-wheel-catalogue", model=VcsBridge, backend_class="local",
        config={"local_root": str(root), "local_name": "local-wheels"},
    )
    bridge.discover_repositories()
    with system_context(reason="tests.catalogue.local_wheel_source"):
        return Source.objects.create(repository=Repository.objects.get(name="local-wheels"), kind="addon", path="dist")


def test_local_catalogue_indexes_only_explicit_built_wheels(composed_tables, tmp_path, monkeypatch):
    directory = tmp_path / "dist"
    directory.mkdir()
    with zipfile.ZipFile(directory / "local_addon-1.0-py3-none-any.whl", "w") as wheel:
        for name, content in wheel_files().items():
            wheel.writestr(name, content)
    (tmp_path / "pyproject.toml").write_text('[build-system]\nrequires=[]\nbuild-backend="must_not_import"\n')
    source = local_source(tmp_path)
    monkeypatch.setattr(catalog.OperatorDaemon, "from_settings", lambda: pytest.fail("local wheels require no daemon"))
    assert source.refresh() == 1
    with system_context(reason="tests.catalogue.local_wheel_read"):
        row = Addon.objects.get(name="local.addon")
        assert row.description == "Built locally" and row.category == "Integration"
        assert row.vcs_path == "dist/local_addon-1.0-py3-none-any.whl"


def test_local_source_does_not_implicitly_build_working_tree(composed_tables, tmp_path):
    (tmp_path / "dist").mkdir()
    source = local_source(tmp_path)
    with pytest.raises(ImproperlyConfigured, match="explicitly built wheels"):
        source.refresh()
    with system_context(reason="tests.catalogue.no_implicit_local_build"):
        source.refresh_from_db()
        assert source.last_synced_at is None


def test_selected_github_connection_owns_the_git_username_and_credential():
    from angee.integrate_github.backend import GitHubBackend

    calls = []
    def selected(*, token_username):
        calls.append(token_username)
        return {"username": token_username, "password": "selected-connection-token"}
    backend = GitHubBackend(SimpleNamespace(credential=SimpleNamespace(git_credentials=selected)))
    assert backend.git_credentials() == {
        "username": "x-access-token", "password": "selected-connection-token",
    }
    assert calls == ["x-access-token"]
