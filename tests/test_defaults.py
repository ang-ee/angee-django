"""Base defaults remain optional native app contributions."""
from __future__ import annotations

from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from angee.base.autoconfig import read_through_parent
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
    for name in ("MIDDLEWARE", "COMPOSE_INTEGRATIONS", "REBAC_STRICT_MODE", "ANGEE_CHANGESET_MODEL"):
        assert name not in namespace


@pytest.mark.parametrize(
    ("authored", "environment", "explicit", "structural", "display"),
    [
        (None, None, {}, False, None),
        ("unset", None, {}, False, None),
        (True, None, {}, True, True),
        (False, None, {}, False, None),
        (None, "true", {}, True, True),
        (None, "1", {}, True, True),
        (True, "false", {}, False, None),
        (True, "0", {}, False, None),
        (True, None, {"REBAC_STRUCTURAL_PREFETCH": False}, False, True),
        (None, None, {"REBAC_STRUCTURAL_PREFETCH": True}, True, None),
        (True, None, {"REBAC_DISPLAY_FIELDS": False}, True, False),
        (True, None, {"REBAC_DISPLAY_FIELDS": "always"}, True, "always"),
        (None, None, {"REBAC_DISPLAY_FIELDS": "fallback"}, False, "fallback"),
    ],
)
def test_read_through_parent_derives_its_library_settings_unless_the_project_sets_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    authored: bool | str | None, environment: str | None, explicit: dict[str, object], structural: bool,
    display: bool | str | None,
) -> None:
    namespace = _through_parent_namespace(tmp_path, monkeypatch, authored, environment, explicit)
    configure(namespace)
    # Read where it is used as at derivation: an environment spelling stays a string in the setting.
    switch = read_through_parent(environment) if environment is not None else authored is True
    assert read_through_parent(namespace["ANGEE_REBAC_READ_THROUGH_PARENT"]) is switch
    # REBAC turns structural prefetch on for the boolean True alone.
    assert namespace["REBAC_STRUCTURAL_PREFETCH"] is structural
    # Display rows: the switch derives True; off, it derives nothing and REBAC keeps its own default.
    assert namespace.get("REBAC_DISPLAY_FIELDS") == display
    assert ("REBAC_DISPLAY_FIELDS" in namespace) is (display is not None)


def test_read_through_parent_refuses_fallback_display_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    namespace = _through_parent_namespace(tmp_path, monkeypatch, True, None, {"REBAC_DISPLAY_FIELDS": "fallback"})
    with pytest.raises(ImproperlyConfigured, match='refuses REBAC_DISPLAY_FIELDS = "fallback"'):
        configure(namespace)


def test_read_through_parent_refuses_a_rebac_without_display_rows_through_a_prefetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rebac.managers import RebacQuerySet

    monkeypatch.delattr(RebacQuerySet, "for_display", raising=False)
    with pytest.raises(ImproperlyConfigured, match="RebacQuerySet.for_display"):
        configure(_through_parent_namespace(tmp_path, monkeypatch, True, None, {}))
    # Without display rows asked for, the switch reads children alone and needs nothing more.
    namespace = _through_parent_namespace(tmp_path, monkeypatch, True, None, {"REBAC_DISPLAY_FIELDS": False})
    configure(namespace)
    assert namespace["REBAC_STRUCTURAL_PREFETCH"] is True


def _through_parent_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    authored: bool | str | None, environment: str | None, explicit: dict[str, object],
) -> dict[str, object]:
    monkeypatch.setenv("DJANGO_COMPOSE_MODE", "write")
    monkeypatch.delenv("ANGEE_REBAC_READ_THROUGH_PARENT", raising=False)
    if environment is not None:
        monkeypatch.setenv("ANGEE_REBAC_READ_THROUGH_PARENT", environment)
    namespace: dict[str, object] = {
        "__file__": str(tmp_path / "settings.py"), "INSTALLED_APPS": ["angee.base"],
        "COMPOSE_OUTPUT_DIR": tmp_path / "runtime", **explicit,
    }
    if authored is not None:
        # "unset" authors None, which reads as off.
        namespace["ANGEE_REBAC_READ_THROUGH_PARENT"] = None if authored == "unset" else authored
    return namespace
