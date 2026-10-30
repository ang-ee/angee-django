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
        "__name__": "project_settings",
        "__file__": str(project / "settings.py"),
        "SECRET_KEY": "runtime-settings-test",
        "INSTALLED_APPS": [],
        "COMPOSE_OUTPUT_DIR": project / "runtime",
        "COMPOSE_OUTPUT_MODULE": "generated_runtime",
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
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
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


def test_source_yaml_overlays_authored_python_values(project: Path) -> None:
    (project / "settings.yaml").write_text("SECRET_KEY: yaml-secret\nDEBUG: true\n")
    namespace = _namespace(project, SECRET_KEY="python-secret", DEBUG=False)
    configure(namespace)
    assert namespace["SECRET_KEY"] == "yaml-secret"
    assert namespace["DEBUG"] is True


def test_explicit_final_yaml_preserves_project_root_provenance(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "settings.yaml").write_text("SECRET_KEY: project-secret\nINSTALLED_APPS: []\n")
    final = project / "local.yaml"
    final.write_text("SECRET_KEY: final-secret\n")
    monkeypatch.setenv("YAMLCONF_CONFFILE", str(final))
    namespace = _namespace(project)
    configure(namespace)
    assert namespace["SECRET_KEY"] == "final-secret"
    assert "SECRET_KEY" not in namespace["ANGEE_PROJECT_YAML_SETTINGS"]
    assert "INSTALLED_APPS" in namespace["ANGEE_PROJECT_YAML_SETTINGS"]
    assert namespace["COMPOSE_ROOTS"] == ()


def test_yamlconf_environment_keeps_native_jsonenv_types(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "settings.yaml").write_text(
        "SECRET_KEY: yaml-secret\nDEBUG:jsonenv: true\nALLOWED_HOSTS:jsonenv: true\n"
    )
    monkeypatch.setenv("YAMLCONF_SECRET_KEY", "env-secret")
    monkeypatch.setenv("YAMLCONF_DEBUG", "false")
    monkeypatch.setenv("YAMLCONF_ALLOWED_HOSTS", '["example.test"]')
    namespace = _namespace(project)
    configure(namespace)
    assert namespace["SECRET_KEY"] == "env-secret"
    assert namespace["DEBUG"] is False
    assert namespace["ALLOWED_HOSTS"] == ["example.test"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('COMPOSE_OUTPUT_DIR: "{BASE_DR}/runtime"\n', "Undefined attribute"),
        ("SECRET_KEY: [\n", "Failed to load"),
    ],
)
def test_native_yaml_errors_propagate_before_composition(project: Path, text: str, message: str) -> None:
    (project / "settings.yaml").write_text(text)
    with pytest.raises(ImproperlyConfigured, match=message):
        configure(_namespace(project))


def test_ancestor_yaml_never_replaces_explicit_project_inputs(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nested = project / "nested"
    nested.mkdir()
    (project / "settings.yaml").write_text("SECRET_KEY: ancestor-secret\n")
    (nested / "settings.yaml").write_text("SECRET_KEY: nested-secret\n")
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(nested))
    namespace = _namespace(nested)
    configure(namespace)
    assert namespace["SECRET_KEY"] == "nested-secret"
    assert Path(namespace["BASE_DIR"]) == nested


def test_source_and_deployment_overlays_preserve_native_source_dir(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = project / "artifact"
    deployment = project / "deployment"
    source.mkdir()
    deployment.mkdir()
    (source / "settings.yaml").write_text(
        "CUSTOM_CONFIG: {path: '{SOURCE_DIR}/assets', value: authored}\nSECRET_KEY: placeholder\n",
    )
    (deployment / "settings.yaml").write_text(
        "SECRET_KEY: deployment-secret\n'CUSTOM_CONFIG:append': {value: deployed}\n"
    )
    monkeypatch.setenv("ANGEE_PROJECT_DIR", str(deployment))
    namespace = _namespace(source)
    configure(namespace)
    assert namespace["SECRET_KEY"] == "deployment-secret"
    assert Path(namespace["BASE_DIR"]) == deployment
    assert namespace["CUSTOM_CONFIG"] == {"path": str(source / "assets"), "value": "deployed"}
    assert "SOURCE_DIR" not in namespace


def test_live_yaml_applies_once_without_reading_runtime_snapshots(project: Path) -> None:
    (project / "settings.yaml").write_text("'CUSTOM_LIST:append': [live]\n")
    runtime = project / "runtime"
    runtime.mkdir()
    (runtime / "settings.yaml").write_text("'CUSTOM_LIST:append': [stale-snapshot]\n")
    namespace = _namespace(project, CUSTOM_LIST=["base"])
    configure(namespace)
    assert namespace["CUSTOM_LIST"] == ["base", "live"]


@pytest.mark.parametrize("configured", [False, True])
def test_native_mailers_ignore_retired_email_inputs(
    project: Path, monkeypatch: pytest.MonkeyPatch, configured: bool
) -> None:
    monkeypatch.setenv("EMAIL_URL", "retired-input-is-not-a-url")
    monkeypatch.setenv("EMAIL_BACKEND", "retired.backend.DoesNotExist")
    if configured:
        (project / "settings.yaml").write_text(
            "MAILERS:\n  default:\n    BACKEND: django.core.mail.backends.locmem.EmailBackend\n"
        )
    namespace = _namespace(project)
    configure(namespace)
    expected = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}} if configured else None
    assert namespace.get("MAILERS") == expected
    assert not any(name.startswith("EMAIL_") for name in namespace)

def test_loader_bootstrap_inputs_expand_without_becoming_django_settings(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import django_yamlconf

    native = django_yamlconf.bootstrap_attributes

    def bootstrap(base_dir: str) -> dict[str, Any]:
        attributes = native(base_dir)
        django_yamlconf.add_attr_info(attributes, "LIBRARY_BOOTSTRAP_ONLY", "native-value")
        return attributes

    monkeypatch.setattr(django_yamlconf, "bootstrap_attributes", bootstrap)
    (project / "settings.yaml").write_text('SECRET_KEY: "{LIBRARY_BOOTSTRAP_ONLY}"\n')
    namespace = _namespace(project)
    configure(namespace)
    assert namespace["SECRET_KEY"] == "native-value"
    assert "LIBRARY_BOOTSTRAP_ONLY" not in namespace
