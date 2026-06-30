"""Django AppConfig dependency resolution for composition."""

from __future__ import annotations

from collections.abc import Iterable

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.addons import addon_contract


class AppGraph:
    """Resolve project addon roots into ordered Django app configs.

    ``resolve`` also annotates each returned config with the composed-graph facts
    a runtime reader (e.g. the platform console) needs but cannot re-derive
    correctly from outside — the graph's owner records them here so consumers
    only read:

    - ``angee_addon_root``: whether the project declared this app as a root
      (``True``) versus pulling it in only through another app's ``depends_on``
      closure (``False``). If a declared root is also another root's dependency,
      the root declaration wins. The root/dependency split is the source of an
      addon's "consumer" vs "required" classification.
    - ``angee_depends_on``: the addon's declared dependency names, normalized
      through :meth:`app_dependencies` (the one parser of that fact).
    - ``angee_forced``: whether any other resolved app depends on this one — the
      composer's reading of "cannot be uninstalled" (framework core + anything
      another installed addon needs). Transitive: ``A→B→C`` forces both ``B`` and
      ``C``. A leaf consumer/host root nothing depends on is not forced.
    """

    def resolve(self, roots: Iterable[str | AppConfig]) -> tuple[AppConfig, ...]:
        """Return root Django apps plus their ``depends_on`` closure, annotated."""

        app_configs_by_name: dict[str, AppConfig] = {}
        aliases: dict[str, str] = {}
        root_names: list[str] = []
        root_name_set: set[str] = set()
        expanded: set[str] = set()

        def register(config: AppConfig) -> AppConfig:
            if config.name in app_configs_by_name:
                raise ImproperlyConfigured(f"Duplicate Django app {config.name!r}")
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
            for dependency in self.app_dependencies(config):
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
            for dependency in sorted(self.app_dependencies(config)):
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
            for dependency in self.app_dependencies(config):
                depended_upon.add(aliases.get(dependency, dependency))
        for config in ordered:
            config.angee_addon_root = config.name in root_name_set
            config.angee_depends_on = self.app_dependencies(config)
            config.angee_forced = config.name in depended_upon
        return tuple(ordered)

    def app_dependencies(self, config: AppConfig) -> tuple[str, ...]:
        """Return the app names or labels the addon's ``addon.toml`` declares."""

        contract = addon_contract(config)
        dependencies = contract.depends_on if contract is not None else ()
        if not all(isinstance(item, str) for item in dependencies):
            raise ImproperlyConfigured("depends_on must be a string or iterable of strings")
        seen: set[str] = set()
        for dependency in dependencies:
            if dependency in seen:
                raise ImproperlyConfigured(f"{config.name} declares duplicate dependency {dependency!r}")
            seen.add(dependency)
        return dependencies
