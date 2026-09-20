"""Tests for Django app dependency resolution across addons and plain apps."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from hatch_angee import AddonManifest

from angee.addons import addon_manifest, resolve_manifest_roots
from angee.compose.appgraph import AppGraph
from tests.conftest import make_addon


def test_folder_addon_dependency_resolves_manifest_free_core_app() -> None:
    """An addon may depend on ``angee.base`` after core stops being an addon."""

    consumer = make_addon(name="example.consumer", depends_on=("angee.base",))

    resolved = AppGraph().resolve((consumer,))
    base, resolved_consumer = resolved

    assert base.name == "angee.base"
    assert addon_manifest(base) is None
    assert not hasattr(base, "angee_depends_on")
    assert base.angee_forced is True
    assert resolved_consumer is consumer


@pytest.mark.parametrize("discovery", ("manifests", "reversed_manifests", "app_configs"))
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
        resolved = AppGraph().resolve(roots)
    else:
        discovered = list(configs.values())
        if discovery == "reversed_manifests":
            discovered.reverse()
        resolved = resolve_manifest_roots(
            (root.name for root in roots),
            (addon_manifest(config) for config in discovered),
        )
    assert [app.name for app in resolved] == [
        "example.shared",
        "example.beta",
        "example.zed",
        "example.alpha",
        "example.independent",
    ]


@pytest.mark.parametrize("manifest_only", (False, True))
@pytest.mark.parametrize("self_cycle", (False, True))
def test_app_dependency_cycles_are_rejected_for_both_discovery_paths(
    tmp_path: Path, manifest_only: bool, self_cycle: bool
) -> None:
    """Both discovery paths report the complete closed cycle in traversal order."""

    alpha = make_addon(
        name="example.alpha",
        path=tmp_path / "alpha",
        depends_on=("example.alpha" if self_cycle else "example.beta",),
    )
    beta = make_addon(name="example.beta", path=tmp_path / "beta", depends_on=(alpha.name,))
    error = RuntimeError if manifest_only else ImproperlyConfigured

    with pytest.raises(error) as caught:
        if manifest_only:
            resolve_manifest_roots((alpha.name,), (addon_manifest(beta), addon_manifest(alpha)))
        else:
            AppGraph().resolve((alpha,))
    cycle = "example.alpha -> example.alpha" if self_cycle else "example.alpha -> example.beta -> example.alpha"
    assert str(caught.value) == f"Cycle in app dependencies: {cycle}"


def test_manifest_dependency_projection_excludes_unselected_cycles_and_plain_apps(tmp_path: Path) -> None:
    """Availability is not activation, and manifest-free dependencies need no import."""

    consumer = make_addon(name="example.consumer", path=tmp_path / "consumer", depends_on=("angee.base",))
    disabled = make_addon(name="example.disabled", path=tmp_path / "disabled", depends_on=("example.disabled",))

    resolved = resolve_manifest_roots(
        (consumer.name, "django.contrib.auth"),
        (addon_manifest(disabled), addon_manifest(consumer)),
    )

    assert [manifest.name for manifest in resolved] == [consumer.name]


@pytest.mark.parametrize("manifest_only", (False, True))
def test_dependency_aliases_resolve_to_the_same_app_order(tmp_path: Path, manifest_only: bool) -> None:
    """Native app-label references address the same graph through either discovery input."""

    consumer = make_addon(name="example.consumer", path=tmp_path / "consumer", depends_on=("dependency",))
    dependency = make_addon(name="example.dependency", path=tmp_path / "dependency")

    if manifest_only:
        resolved = resolve_manifest_roots(
            (consumer.name, dependency.name),
            (addon_manifest(consumer), addon_manifest(dependency)),
            aliases={dependency.label: dependency.name},
        )
    else:
        resolved = AppGraph().resolve((consumer, dependency))

    assert [app.name for app in resolved] == [dependency.name, consumer.name]


@pytest.mark.parametrize(
    ("roots", "dependencies", "message"),
    (
        (("example.consumer", "example.consumer"), (), "Duplicate root app"),
        (("example.consumer",), ("angee.base", "angee.base"), "declares duplicate dependency"),
    ),
)
def test_manifest_graph_rejects_duplicate_declarations(
    roots: tuple[str, ...], dependencies: tuple[str, ...], message: str
) -> None:
    """Manifest-only previews reject duplicate declarations before projecting plain apps away."""

    consumer = AddonManifest(name="example.consumer", depends_on=dependencies)

    with pytest.raises(RuntimeError, match=message):
        resolve_manifest_roots(roots, (consumer,))


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

    assert AppGraph().resolve((consumer,)) == (dependency, consumer)
    assert created == [declaration]
    assert dependency.angee_forced is True


def test_appgraph_unknown_dependency_still_fails_during_discovery(tmp_path: Path) -> None:
    """The manifest projection's omission rule cannot hide a missing installed app."""

    consumer = make_addon(
        name="example.consumer", path=tmp_path / "consumer", depends_on=("missing_app_for_graph_test",)
    )

    with pytest.raises(ImproperlyConfigured, match="depends on unknown app 'missing_app_for_graph_test'"):
        AppGraph().resolve((consumer,))
