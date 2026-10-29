"""Tests for Django app dependency resolution across addons and plain apps."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.addons import addon_dependencies, is_angee_addon, resolve_package_roots
from angee.build import PackageDeclarations
from angee.compose.appgraph import AppGraph
from angee.compose.declarations import AppDeclarationSource
from tests.conftest import make_addon


def _package(config: AppConfig) -> dict[str, object]:
    return {"name": config.name, "depends_on": addon_dependencies(config)}


def test_folder_addon_dependency_resolves_package_free_core_app() -> None:
    """An addon may depend on ``angee`` after core stops being an addon."""

    consumer = make_addon(name="example.consumer", depends_on=("angee",))

    resolved = AppGraph(PackageDeclarations()).resolve((consumer,))
    base, resolved_consumer = resolved

    assert base.name == "angee"
    assert not is_angee_addon(base)
    assert not hasattr(base, "angee_depends_on")
    assert base.compose_required is True
    assert resolved_consumer is consumer


@pytest.mark.parametrize("discovery", ("packages", "reversed_packages", "app_configs"))
def test_app_dependency_order_preserves_root_and_dependency_precedence(tmp_path: Path, discovery: str) -> None:
    """Both discovery paths complete each sorted dependency subtree in root order."""

    declarations = {
        "alpha": ("example.zed", "example.beta"),
        "beta": ("example.shared",),
        "shared": (),
        "zed": (),
        "independent": (),
    }
    configs = {
        name: make_addon(name=f"example.{name}", path=tmp_path / name, depends_on=dependencies)
        for name, dependencies in declarations.items()
    }
    roots = (configs["alpha"], configs["independent"])
    if discovery == "app_configs":
        resolved = AppGraph(PackageDeclarations()).resolve(roots)
    else:
        discovered = list(configs.values())
        if discovery == "reversed_packages":
            discovered.reverse()
        resolved = resolve_package_roots(
            (root.name for root in roots),
            (_package(config) for config in discovered),
        )
    assert [app.name if isinstance(app, AppConfig) else app["name"] for app in resolved] == [
        "example.shared",
        "example.beta",
        "example.zed",
        "example.alpha",
        "example.independent",
    ]


def test_fixture_foreign_dependencies_survive_native_config_rediscovery(tmp_path: Path) -> None:
    """A fixture config's class owns edges needed before their packages are installed."""

    declared = make_addon(name="example.forward", path=tmp_path / "forward", depends_on=("example.required",))
    rediscovered = AppConfig.create(declared.name)

    assert type(rediscovered) is type(declared)
    assert addon_dependencies(rediscovered) == ("example.required",)


@pytest.mark.parametrize("catalogue_only", (False, True))
@pytest.mark.parametrize("self_cycle", (False, True))
def test_app_dependency_cycles_are_rejected_for_both_discovery_paths(
    tmp_path: Path, catalogue_only: bool, self_cycle: bool
) -> None:
    """Both discovery paths report the complete closed cycle in traversal order."""

    alpha = make_addon(
        name="example.alpha",
        path=tmp_path / "alpha",
        depends_on=("example.alpha" if self_cycle else "example.beta",),
    )
    beta = make_addon(name="example.beta", path=tmp_path / "beta", depends_on=(alpha.name,))
    error = RuntimeError if catalogue_only else ImproperlyConfigured

    with pytest.raises(error) as caught:
        if catalogue_only:
            resolve_package_roots((alpha.name,), (_package(beta), _package(alpha)))
        else:
            AppGraph(PackageDeclarations()).resolve((alpha,))
    cycle = "example.alpha -> example.alpha" if self_cycle else "example.alpha -> example.beta -> example.alpha"
    assert str(caught.value) == f"Cycle in app dependencies: {cycle}"


def test_package_dependency_projection_excludes_unselected_cycles_and_plain_apps(tmp_path: Path) -> None:
    """Availability is not activation, and addon-free dependencies need no import."""

    consumer = make_addon(name="example.consumer", path=tmp_path / "consumer", depends_on=("angee",))
    disabled = make_addon(name="example.disabled", path=tmp_path / "disabled", depends_on=("example.disabled",))

    resolved = resolve_package_roots(
        (consumer.name, "django.contrib.auth"),
        (_package(disabled), _package(consumer)),
    )

    assert [package["name"] for package in resolved] == [consumer.name]


@pytest.mark.parametrize("catalogue_only", (False, True))
def test_dependency_aliases_resolve_to_the_same_app_order(tmp_path: Path, catalogue_only: bool) -> None:
    """Native app-label references address the same graph through either discovery input."""

    consumer = make_addon(name="example.consumer", path=tmp_path / "consumer", depends_on=("dependency",))
    dependency = make_addon(name="example.dependency", path=tmp_path / "dependency")

    if catalogue_only:
        resolved = resolve_package_roots(
            (consumer.name, dependency.name),
            (_package(consumer), _package(dependency)),
            aliases={dependency.label: dependency.name},
        )
    else:
        resolved = AppGraph(PackageDeclarations()).resolve((consumer, dependency))

    assert [app.name if isinstance(app, AppConfig) else app["name"] for app in resolved] == [
        dependency.name, consumer.name,
    ]


@pytest.mark.parametrize(
    ("roots", "dependencies", "message"),
    (
        (("example.consumer", "example.consumer"), (), "Duplicate root app"),
        (("example.consumer",), ("angee", "angee"), "declares duplicate dependency"),
    ),
)
def test_package_graph_rejects_duplicate_declarations(
    roots: tuple[str, ...], dependencies: tuple[str, ...], message: str
) -> None:
    """Catalogue previews reject duplicate declarations before projecting plain apps away."""

    consumer = {"name": "example.consumer", "depends_on": dependencies}

    with pytest.raises(RuntimeError, match=message):
        resolve_package_roots(roots, (consumer,))


def test_appgraph_retains_discovered_app_config_dependency_alias(tmp_path: Path, monkeypatch) -> None:
    """Django's resolved config identity survives an exact AppConfig declaration."""

    declaration = "example.dependency.apps.DependencyConfig"
    dependency = make_addon(name="example.dependency", path=tmp_path / "dependency")
    consumer = make_addon(name="example.consumer", path=tmp_path / "consumer", depends_on=(declaration,))
    created: list[str] = []

    def create(name: str) -> AppConfig:
        created.append(name)
        assert name == declaration
        return dependency

    monkeypatch.setattr(AppConfig, "create", create)

    assert AppGraph(PackageDeclarations()).resolve((consumer,)) == (dependency, consumer)
    assert created == [declaration]
    assert dependency.compose_required is True


def test_appgraph_unknown_dependency_still_fails_during_discovery(tmp_path: Path) -> None:
    """The package projection's omission rule cannot hide a missing installed app."""

    consumer = make_addon(
        name="example.consumer", path=tmp_path / "consumer", depends_on=("missing_app_for_graph_test",)
    )

    with pytest.raises(ImproperlyConfigured, match="depends on unknown app 'missing_app_for_graph_test'"):
        AppGraph(PackageDeclarations()).resolve((consumer,))


def test_plain_composer_has_no_implicit_package_policy(tmp_path: Path) -> None:
    """A host must supply package semantics through the explicit adapter."""

    consumer = make_addon(name="example.consumer", path=tmp_path, depends_on=("missing.app",))
    (tmp_path / "pyproject.toml").write_text("not valid toml = [")
    assert AppGraph(AppDeclarationSource()).resolve((consumer,)) == (consumer,)
