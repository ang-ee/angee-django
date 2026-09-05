"""Django AppConfig dependency resolution for composition."""

from __future__ import annotations

from collections.abc import Iterable

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.addons import addon_manifest


class AppGraph:
    """Resolve settings app roots into ordered Django app configs.

    ``resolve`` also annotates each returned config with composed-graph facts.
    Runtime addon readers (e.g. the platform console) cannot re-derive those
    correctly from outside, so the graph's owner records them here and consumers
    only read:

    - ``angee_addon_root``: whether the project declared this app as a root
      (``True``) versus pulling it in only through another app's ``depends_on``
      closure (``False``). If a declared root is also another root's dependency,
      the root declaration wins. The root/dependency split is the source of an
      addon's "consumer" vs "required" classification.
    - ``angee_forced``: whether any other resolved app depends on this one — the
      composer's reading of "cannot be uninstalled" for addons another installed
      addon needs. Transitive: ``A→B→C`` forces both ``B`` and ``C``. A leaf
      consumer/host root nothing depends on is not forced.
    """

    def resolve(self, roots: Iterable[str | AppConfig]) -> tuple[AppConfig, ...]:
        """Return root Django apps plus their ``depends_on`` closure, annotated."""

        app_configs_by_name: dict[str, AppConfig] = {}
        aliases: dict[str, str] = {}
        dependencies_by_name: dict[str, tuple[str, ...]] = {}
        root_names: list[str] = []
        root_name_set: set[str] = set()
        expanded: set[str] = set()

        def register(config: AppConfig) -> AppConfig:
            if config.name in app_configs_by_name:
                raise ImproperlyConfigured(f"Duplicate Django app {config.name!r}")
            manifest = addon_manifest(config, refresh=True)
            dependencies_by_name[config.name] = manifest.depends_on if manifest is not None else ()
            app_configs_by_name[config.name] = config
            for alias in (config.name, config.label):
                existing = aliases.setdefault(alias, config.name)
                if existing != config.name:
                    raise ImproperlyConfigured(f"Duplicate app alias {alias!r}")
            return config

        def create_app_config(app_name: str, *, owner: AppConfig | None = None) -> AppConfig:
            try:
                return AppConfig.create(app_name)
            except ImportError as error:
                if owner is not None:
                    raise ImproperlyConfigured(f"{owner.name} depends on unknown app {app_name!r}") from error
                raise

        def include_dependencies(config: AppConfig) -> None:
            if config.name in expanded:
                return
            expanded.add(config.name)
            for dependency in dependencies_by_name[config.name]:
                dependency_name = aliases.get(dependency, dependency)
                dependency_config = app_configs_by_name.get(dependency_name)
                if dependency_config is None:
                    dependency_config = create_app_config(dependency_name, owner=config)
                    dependency_config = app_configs_by_name.get(dependency_config.name) or register(dependency_config)
                include_dependencies(dependency_config)

        def visit_app(name: str, *, ordered: list[AppConfig], visiting: set[str], visited: set[str]) -> None:
            if name in visited:
                return
            if name in visiting:
                raise ImproperlyConfigured(f"Cycle in app dependencies at {name}")
            visiting.add(name)
            config = app_configs_by_name[name]
            for dependency in sorted(dependencies_by_name[config.name]):
                dependency_name = aliases.get(dependency)
                if dependency_name is None:
                    raise ImproperlyConfigured(f"{config.name} depends on unknown app {dependency!r}")
                visit_app(dependency_name, ordered=ordered, visiting=visiting, visited=visited)
            visiting.remove(name)
            visited.add(name)
            ordered.append(config)

        for root in roots:
            config = root if isinstance(root, AppConfig) else create_app_config(aliases.get(root, root))
            if config.name in root_name_set:
                raise ImproperlyConfigured(f"Duplicate root app {config.name!r}")
            if config.name in app_configs_by_name:
                root_names.append(config.name)
                continue
            root_name = register(config).name
            root_names.append(root_name)
            root_name_set.add(root_name)

        for name in tuple(root_names):
            include_dependencies(app_configs_by_name[name])

        ordered: list[AppConfig] = []
        visiting: set[str] = set()
        visited: set[str] = set()
        for name in root_names:
            visit_app(name, ordered=ordered, visiting=visiting, visited=visited)
        for name in sorted(app_configs_by_name):
            visit_app(name, ordered=ordered, visiting=visiting, visited=visited)

        depended_upon: set[str] = set()
        for config in ordered:
            for dependency in dependencies_by_name[config.name]:
                depended_upon.add(aliases.get(dependency, dependency))
        for config in ordered:
            config.angee_addon_root = config.name in root_name_set
            config.angee_forced = config.name in depended_upon
        return tuple(ordered)
