"""Authored settings and deployment overlays retain native compiler ownership."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from django.core.exceptions import ImproperlyConfigured

from angee.base.conf import configure


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("YAMLCONF_CONFFILE", raising=False)
    return tmp_path


def _namespace(project: Path, **values: Any) -> dict[str, Any]:
    return {
        "__name__": "project_settings", "__file__": str(project / "settings.py"),
        "SECRET_KEY": "runtime-settings-test", "INSTALLED_APPS": [],
        "COMPOSE_OUTPUT_DIR": project / "runtime", "COMPOSE_OUTPUT_MODULE": "generated_runtime",
        **values,
    }


@pytest.mark.parametrize("source", ["python", "yaml"])
def test_host_publishes_one_canonical_runtime_contract(project: Path, source: str) -> None:
    values = {"COMPOSE_OUTPUT_DIR": str(project / "runtime"), "COMPOSE_OUTPUT_MODULE": "generated_runtime"}
    namespace = _namespace(project)
    if source == "python":
        namespace.update(values)
    else:
        (project / "settings.yaml").write_text(yaml.safe_dump(values))
    configure(namespace)
    assert Path(namespace["COMPOSE_OUTPUT_DIR"]) == project / "runtime"
    assert namespace["COMPOSE_OUTPUT_MODULE"] == "generated_runtime"
    assert "COMPOSE_RUNTIME_DIR" not in namespace
    assert "COMPOSE_RUNTIME_MODULE" not in namespace


def test_yaml_references_keep_native_expansion_after_addon_defaults(project: Path) -> None:
    (project / "settings.yaml").write_text(
        'COMPOSE_OUTPUT_DIR: "{BASE_DIR}/runtime"\nARCHIVE_DIRECTORY: "{COMPOSE_OUTPUT_DIR}/archive"\n',
    )
    namespace = _namespace(project)
    configure(namespace)
    assert Path(namespace["COMPOSE_OUTPUT_DIR"]) == project / "runtime"
    assert namespace["ARCHIVE_DIRECTORY"] == str(project / "runtime/archive")


def test_environment_cannot_replace_authored_compiler_structure(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Native protected-input validation replaces the retired environment override."""
    declarations = {"COMPOSE_OUTPUT_DIR": project / "runtime", "COMPOSE_OUTPUT_MODULE": "generated_runtime"}
    monkeypatch.setenv("YAMLCONF_COMPOSE_OUTPUT_DIR", str(project / "overridden"))
    namespace = _namespace(project, **declarations)
    with pytest.raises(ImproperlyConfigured, match="environment"):
        configure(namespace)
    assert declarations == {"COMPOSE_OUTPUT_DIR": project / "runtime", "COMPOSE_OUTPUT_MODULE": "generated_runtime"}
    monkeypatch.delenv("YAMLCONF_COMPOSE_OUTPUT_DIR")
    namespace = _namespace(project, **declarations)
    configure(namespace)
    assert Path(namespace["COMPOSE_OUTPUT_DIR"]) == project / "runtime"


def test_nested_environment_override_does_not_mutate_python_declarations(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = {"cache": {"ttl": 10, "enabled": True}}
    monkeypatch.setenv("YAMLCONF_OPTIONS.cache.ttl", "20")
    namespace = _namespace(project, OPTIONS=options)
    configure(namespace)
    assert namespace["OPTIONS"] == {"cache": {"ttl": "20", "enabled": True}}
    assert options == {"cache": {"ttl": 10, "enabled": True}}
    monkeypatch.delenv("YAMLCONF_OPTIONS.cache.ttl")
    namespace = _namespace(project, OPTIONS=options)
    configure(namespace)
    assert namespace["OPTIONS"] == options
