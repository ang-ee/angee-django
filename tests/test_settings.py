"""Tests for the ``compose_defaults`` host settings helper."""

from __future__ import annotations

from pathlib import Path

from angee.base.settings import compose_defaults


def _compose(tmp_path: Path) -> dict[str, object]:
    """Return composed settings for the notes example addon."""

    return compose_defaults(
        addons=("example.notes",),
        runtime_dir=tmp_path / "runtime",
        data_dir=tmp_path / "data",
        root_urlconf="host.urls",
        asgi_application="host.asgi.application",
    )


def test_base_migrations_are_redirected_into_runtime(tmp_path: Path) -> None:
    """Base is composed like any other addon: its migrations live in runtime.

    ``Resource`` is an abstract source model emitted into ``runtime/base``; its
    concrete migration must land in ``runtime.base.migrations`` and be
    normalized, not in the installed package source tree.
    """

    settings = compose_defaults(
        addons=("example.notes",),
        runtime_dir=tmp_path / "runtime",
        data_dir=tmp_path / "data",
        root_urlconf="host.urls",
        asgi_application="host.asgi.application",
    )

    migration_modules = settings["MIGRATION_MODULES"]
    assert migration_modules["base"] == "runtime.base.migrations"
    assert migration_modules["notes"] == "runtime.notes.migrations"


def test_base_is_installed_exactly_once(tmp_path: Path) -> None:
    """Redirecting base migrations keeps it listed once in INSTALLED_APPS."""

    settings = _compose(tmp_path)
    installed = settings["INSTALLED_APPS"]
    base_app = "angee.base.apps.BaseConfig"
    assert installed.count(base_app) == 1


def test_run_app_set_installs_resources_without_compose(
    tmp_path: Path,
) -> None:
    """Run settings install runtime and resource command hosts."""

    settings = _compose(tmp_path)
    installed = settings["INSTALLED_APPS"]

    assert installed.count("angee.base.apps.BaseConfig") == 1
    assert installed.count("angee.resources.apps.ResourcesConfig") == 1
    assert "angee.compose.apps.ComposeConfig" not in installed
    assert settings["ANGEE_BUILD"] is False


def test_rebac_strict_mode_is_explicitly_pinned(tmp_path: Path) -> None:
    """Composed hosts keep REBAC strict mode enabled by default."""

    settings = _compose(tmp_path)

    assert settings["REBAC_STRICT_MODE"] is True


def test_build_app_set_installs_compose_without_runtime_apps(
    tmp_path: Path,
) -> None:
    """Build settings install source addons and the compose command host."""

    settings = compose_defaults(
        addons=("example.notes",),
        runtime_dir=tmp_path / "runtime",
        data_dir=tmp_path / "data",
        root_urlconf="host.urls",
        asgi_application="host.asgi.application",
        build=True,
    )
    installed = settings["INSTALLED_APPS"]

    assert "angee.compose.apps.ComposeConfig" in installed
    assert "angee.base.apps.BaseConfig" in installed
    assert "angee.resources.apps.ResourcesConfig" not in installed
    assert settings["ANGEE_BUILD"] is True
