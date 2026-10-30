"""Base defaults remain optional native app contributions."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.base.conf import configure


def test_base_defaults_do_not_install_http_or_execution_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    namespace = {
        "__file__": str(tmp_path / "settings.py"), "INSTALLED_APPS": ["angee.base"],
        "COMPOSE_OUTPUT_DIR": tmp_path / "runtime", "SECRET_KEY": "base-defaults",
    }
    configure(namespace)
    assert namespace["COMPOSE_ROOTS"] == ("angee.base",)
    assert namespace["USE_TZ"] is True
    assert namespace["DEFAULT_AUTO_FIELD"] == "django.db.models.BigAutoField"
    for name in ("ROOT_URLCONF", "ASGI_APPLICATION", "CELERY_TASK_SOFT_TIME_LIMIT", "ANGEE_CHANGESET_MODEL"):
        assert name not in namespace


def test_empty_roots_do_not_install_base_implicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    namespace = {
        "__file__": str(tmp_path / "settings.py"), "INSTALLED_APPS": [],
        "COMPOSE_OUTPUT_DIR": tmp_path / "runtime",
    }
    configure(namespace)
    assert namespace["COMPOSE_CLOSURE"].app_configs == ()
    assert "REBAC_STRICT_MODE" not in namespace


@pytest.mark.parametrize(
    ("engine", "depth", "environment_depth", "expected"),
    [(None, None, None, 8), ("sqlite3", None, None, 8), ("postgresql", None, None, 16),
     ("sqlite3", 4, None, 4), ("postgresql", 4, None, 4), ("sqlite3", None, "12", 12),
     ("postgresql", None, "12", 12), ("sqlite3", 4, "12", 12), ("postgresql", 4, "12", 12)],
)
def test_rebac_depth_uses_explicit_settings_before_backend_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    engine: str | None, depth: int | None, environment_depth: str | None, expected: int,
) -> None:
    # Deployment settings use native YAMLCONF_*; direct REBAC_* inputs are retired.
    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    monkeypatch.delenv("YAMLCONF_REBAC_DEPTH_LIMIT", raising=False)
    if environment_depth is not None:
        monkeypatch.setenv("YAMLCONF_REBAC_DEPTH_LIMIT", environment_depth)
        (tmp_path / "settings.yaml").write_text("REBAC_DEPTH_LIMIT:jsonenv: true\n")
    namespace = {
        "__file__": str(tmp_path / "settings.py"), "INSTALLED_APPS": ["angee.base"],
        "COMPOSE_OUTPUT_DIR": tmp_path / "runtime",
    }
    if engine is not None:
        namespace["DATABASES"] = {"default": {"ENGINE": f"django.db.backends.{engine}", "NAME": "test"}}
    if depth is not None:
        namespace["REBAC_DEPTH_LIMIT"] = depth
    configure(namespace)
    assert namespace["REBAC_DEPTH_LIMIT"] == expected
    assert isinstance(namespace["REBAC_DEPTH_LIMIT"], int)


def test_plain_native_composer_preserves_host_choices_without_capability_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from django_compose.conf import configure as compose_configure

    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    namespace = {
        "INSTALLED_APPS": [], "COMPOSE_OUTPUT_DIR": tmp_path / "runtime",
        "ROOT_URLCONF": "project.urls", "ASGI_APPLICATION": "project.asgi.application",
    }
    compose_configure(namespace)
    assert namespace["ROOT_URLCONF"] == "project.urls"
    assert namespace["ASGI_APPLICATION"] == "project.asgi.application"
    for name in ("MIDDLEWARE", "COMPOSE_INTEGRATIONS", "REBAC_BACKEND", "ANGEE_CHANGESET_MODEL"):
        assert name not in namespace


@pytest.fixture
def baseline_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real installed package whose transition modules must stay unimported."""
    package = tmp_path / "baseline_package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    document = package / "pyproject.toml"
    document.write_text('[tool.angee.migrations.initial_baseline]\nlegacy_schema = "satisfied"\n')
    transitions = package / "compose_migrations" / "baseline_package"
    transitions.mkdir(parents=True)
    for name in ("legacy_schema", "real_bootstrap"):
        (transitions / f"{name}.py").write_text('raise AssertionError("settings imported a transition")\n')
    info = tmp_path / "baseline_package-1.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: baseline-package\nVersion: 1\nRequires-Dist: angee-base>=3,<4\n"
    )
    (info / "entry_points.txt").write_text("[angee.addons]\nbaseline_package = baseline_package\n")
    (info / "RECORD").write_text("baseline_package/pyproject.toml,,\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    namespace = {
        "__file__": str(tmp_path / "settings.py"), "INSTALLED_APPS": ["baseline_package"],
        "COMPOSE_OUTPUT_DIR": tmp_path / "runtime", "SECRET_KEY": "baseline-declarations",
    }
    yield namespace, document
    for name in tuple(sys.modules):
        if name == "baseline_package" or name.startswith("baseline_package."):
            del sys.modules[name]


def test_baseline_metadata_annotates_native_discovery_without_importing_transitions(baseline_package) -> None:
    namespace, _ = baseline_package
    configure(namespace)
    records = namespace["COMPOSE_SNAPSHOT"].settings["COMPOSE_TRANSITION_SOURCE"]
    assert [(record["name"], record.get("initial_baseline")) for record in records] == [
        ("legacy_schema", "satisfied"), ("real_bootstrap", None),
    ]
    assert all(record["declaring_app"] == "baseline_package" for record in records)


def test_explicit_project_transition_source_preserves_owner_choice(baseline_package) -> None:
    namespace, document = baseline_package
    document.write_text('[tool.angee.migrations.initial_baseline]\nremoved_transition = "satisfied"\n')
    namespace["COMPOSE_TRANSITION_SOURCE"] = ()
    configure(namespace)
    assert namespace["COMPOSE_SNAPSHOT"].settings["COMPOSE_TRANSITION_SOURCE"] == ()


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        ('removed_transition = "satisfied"', "initial_baseline names unknown transition"),
        ('legacy_schema = "skip"', "Transition initial_baseline must be 'satisfied' or absent"),
        ('legacy_schema = true', "Invalid transition declaration"),
    ],
)
def test_invalid_baseline_declaration_refuses_configuration(baseline_package, declaration, message) -> None:
    namespace, document = baseline_package
    document.write_text(f"[tool.angee.migrations.initial_baseline]\n{declaration}\n")
    with pytest.raises(ImproperlyConfigured, match=message):
        configure(namespace)
