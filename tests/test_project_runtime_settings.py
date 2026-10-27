"""Host upgrades preserve project declarations while the compiler has one setting surface."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.core.exceptions import ImproperlyConfigured

from angee.conf import ProjectContract
from angee.project import PROJECT_DIR_ENV, PROJECT_SETTINGS_ENV

MODULE = "runtime_settings_project"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv(PROJECT_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(PROJECT_SETTINGS_ENV, MODULE)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, MODULE, raising=False)
    monkeypatch.delenv("YAMLCONF_CONFFILE", raising=False)
    for prefix in ("ANGEE", "COMPOSE"):
        for suffix in ("DIR", "MODULE"):
            monkeypatch.delenv(f"YAMLCONF_{prefix}_RUNTIME_{suffix}", raising=False)
    (tmp_path / "manage.py").write_text("# isolated project entrypoint\n")
    yield tmp_path
    sys.modules.pop(MODULE, None)


@pytest.mark.parametrize("prefix", ["ANGEE", "COMPOSE"])
@pytest.mark.parametrize("source", ["python", "yaml"])
def test_host_publishes_one_canonical_runtime_contract(project: Path, prefix: str, source: str) -> None:
    values = {
        "SECRET_KEY": "runtime-settings-test",
        "INSTALLED_APPS": [],
        f"{prefix}_RUNTIME_DIR": str(project / "runtime"),
        f"{prefix}_RUNTIME_MODULE": "generated_runtime",
    }
    if source == "python":
        (project / f"{MODULE}.py").write_text("\n".join(f"{name} = {value!r}" for name, value in values.items()))
    else:
        (project / "settings.yaml").write_text(yaml.safe_dump(values))
    namespace: dict[str, Any] = {}
    ProjectContract(namespace).compose()
    assert namespace["COMPOSE_RUNTIME_DIR"] == project / "runtime"
    assert namespace["COMPOSE_RUNTIME_MODULE"] == "generated_runtime"
    assert "ANGEE_RUNTIME_DIR" not in namespace
    assert "ANGEE_RUNTIME_MODULE" not in namespace


def test_legacy_yaml_references_keep_native_expansion_after_addon_defaults(project: Path) -> None:
    (project / "settings.yaml").write_text(
        'SECRET_KEY: runtime-settings-test\nINSTALLED_APPS: []\n'
        'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"\n'
        'ARCHIVE_DIRECTORY: "{ANGEE_RUNTIME_DIR}/archive"\n'
    )
    namespace: dict[str, Any] = {}
    ProjectContract(namespace).compose()
    assert namespace["COMPOSE_RUNTIME_DIR"] == project / "runtime"
    assert namespace["ARCHIVE_DIRECTORY"] == str(project / "runtime/archive")
    assert "ANGEE_RUNTIME_DIR" not in namespace


def test_equivalent_declared_runtime_paths_have_one_effective_value(project: Path) -> None:
    (project / "settings.yaml").write_text(
        'SECRET_KEY: runtime-settings-test\nINSTALLED_APPS: []\n'
        'ANGEE_RUNTIME_DIR: "{BASE_DIR}/temporary/../runtime"\n'
        'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"\n'
        'ANGEE_RUNTIME_MODULE: runtime\nCOMPOSE_RUNTIME_MODULE: runtime\n'
    )
    namespace: dict[str, Any] = {}
    ProjectContract(namespace).compose()
    assert namespace["COMPOSE_RUNTIME_DIR"] == project / "runtime"
    assert namespace["COMPOSE_RUNTIME_MODULE"] == "runtime"
    assert "ANGEE_RUNTIME_DIR" not in namespace
    assert "ANGEE_RUNTIME_MODULE" not in namespace


@pytest.mark.parametrize("suffix", ["DIR", "MODULE"])
def test_conflicting_legacy_and_canonical_declarations_refuse_boot(project: Path, suffix: str) -> None:
    (project / "settings.yaml").write_text(yaml.safe_dump({
        "SECRET_KEY": "runtime-settings-test", "INSTALLED_APPS": [],
        "COMPOSE_RUNTIME_DIR": str(project / "runtime"),
        f"ANGEE_RUNTIME_{suffix}": "older", f"COMPOSE_RUNTIME_{suffix}": "different",
    }))
    with pytest.raises(ImproperlyConfigured, match=f"ANGEE_RUNTIME_{suffix} and COMPOSE_RUNTIME_{suffix} must agree"):
        ProjectContract({}).compose()


@pytest.mark.parametrize("prefix", ["ANGEE", "COMPOSE"])
def test_environment_override_does_not_leak_into_cached_python_declarations(
    project: Path, monkeypatch: pytest.MonkeyPatch, prefix: str,
) -> None:
    (project / f"{MODULE}.py").write_text(
        "SECRET_KEY = 'runtime-settings-test'\nINSTALLED_APPS = []\n"
        f"{prefix}_RUNTIME_DIR = {str(project / 'runtime')!r}\n"
        f"{prefix}_RUNTIME_MODULE = 'declared_runtime'\n"
    )
    monkeypatch.setenv(f"YAMLCONF_{prefix}_RUNTIME_DIR", str(project / "overridden"))
    monkeypatch.setenv(f"YAMLCONF_{prefix}_RUNTIME_MODULE", "environment_runtime")
    namespace: dict[str, Any] = {}
    contract = ProjectContract(namespace)
    contract.compose()
    assert namespace["COMPOSE_RUNTIME_DIR"] == project / "overridden"
    assert namespace["COMPOSE_RUNTIME_MODULE"] == "environment_runtime"
    monkeypatch.delenv(f"YAMLCONF_{prefix}_RUNTIME_DIR")
    monkeypatch.delenv(f"YAMLCONF_{prefix}_RUNTIME_MODULE")
    contract.compose()
    assert namespace["COMPOSE_RUNTIME_DIR"] == project / "runtime"
    assert namespace["COMPOSE_RUNTIME_MODULE"] == "declared_runtime"
    assert "ANGEE_RUNTIME_DIR" not in namespace
    assert "ANGEE_RUNTIME_MODULE" not in namespace
    assert getattr(sys.modules[MODULE], f"{prefix}_RUNTIME_MODULE") == "declared_runtime"


def test_nested_environment_override_does_not_mutate_python_declarations(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project / f"{MODULE}.py").write_text(
        "import pathlib\nSECRET_KEY = 'runtime-settings-test'\nINSTALLED_APPS = []\n"
        f"COMPOSE_RUNTIME_DIR = {str(project / 'runtime')!r}\n"
        "OPTIONS = {'cache': {'ttl': 10, 'enabled': True}}\n"
    )
    monkeypatch.setenv("YAMLCONF_OPTIONS.cache.ttl", "20")
    namespace: dict[str, Any] = {}
    contract = ProjectContract(namespace)
    contract.compose()
    assert namespace["OPTIONS"] == {"cache": {"ttl": "20", "enabled": True}}
    assert sys.modules[MODULE].OPTIONS == {"cache": {"ttl": 10, "enabled": True}}
    monkeypatch.delenv("YAMLCONF_OPTIONS.cache.ttl")
    contract.compose()
    assert namespace["OPTIONS"] == {"cache": {"ttl": 10, "enabled": True}}
