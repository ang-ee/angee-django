"""Django AppConfig dependency resolution for composition."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class AppGraph:
    """Resolve project addon roots into ordered Django app configs."""

    def __init__(self) -> None:
        """Create an empty dependency graph."""

        self.app_configs_by_name: dict[str, AppConfig] = {}

    def resolve(self, roots: Iterable[str | AppConfig]) -> tuple[AppConfig, ...]:
        """Return root Django apps plus their ``depends_on`` closure."""

        self.app_configs_by_name = {}
        root_names: list[str] = []
        for root in roots:
            root_names.append(self.include_app(root).name)

        ordered: list[AppConfig] = []
        visiting: set[str] = set()
        visited: set[str] = set()
        aliases = self.app_aliases()
        for name in root_names:
            self.visit_app(name, aliases=aliases, ordered=ordered, visiting=visiting, visited=visited)
        for name in sorted(self.app_configs_by_name):
            self.visit_app(name, aliases=aliases, ordered=ordered, visiting=visiting, visited=visited)
        return tuple(ordered)

    def include_app(self, app: str | AppConfig, *, owner: AppConfig | None = None) -> AppConfig:
        """Include one Django app config and its dependencies."""

        app_name = app.name if isinstance(app, AppConfig) else app
        app_name = self.app_aliases().get(app_name, app_name)
        if app_name in self.app_configs_by_name:
            return self.app_configs_by_name[app_name]
        try:
            config = AppConfig.create(app_name)
        except ImportError as error:
            if owner is not None:
                raise ImproperlyConfigured(f"{owner.name} depends on unknown app {app_name!r}") from error
            raise
        if config.name in self.app_configs_by_name:
            raise ImproperlyConfigured(f"Duplicate Django app {config.name!r}")
        self.app_configs_by_name[config.name] = config
        for dependency in self.app_dependencies(config):
            self.include_app(dependency, owner=config)
        return config

    def visit_app(
        self,
        name: str,
        *,
        aliases: Mapping[str, str],
        ordered: list[AppConfig],
        visiting: set[str],
        visited: set[str],
    ) -> None:
        """Append one app config after its included dependencies."""

        if name in visited:
            return
        if name in visiting:
            raise ImproperlyConfigured(f"Cycle in app dependencies at {name}")
        visiting.add(name)
        config = self.app_configs_by_name[name]
        for dependency in sorted(self.app_dependencies(config)):
            dependency_name = aliases.get(dependency)
            if dependency_name is None:
                raise ImproperlyConfigured(f"{config.name} depends on unknown app {dependency!r}")
            self.visit_app(
                dependency_name,
                aliases=aliases,
                ordered=ordered,
                visiting=visiting,
                visited=visited,
            )
        visiting.remove(name)
        visited.add(name)
        ordered.append(config)

    def app_aliases(self) -> dict[str, str]:
        """Return app names and labels mapped to canonical app names."""

        aliases: dict[str, str] = {}
        for config in self.app_configs_by_name.values():
            for alias in (config.name, config.label):
                existing = aliases.setdefault(alias, config.name)
                if existing != config.name:
                    raise ImproperlyConfigured(f"Duplicate app alias {alias!r}")
        return aliases

    def app_dependencies(self, config: AppConfig) -> tuple[str, ...]:
        """Return the app names or labels declared in ``depends_on``."""

        value = getattr(config, "depends_on", ())
        if isinstance(value, str):
            return (value,)
        if not isinstance(value, Iterable):
            raise ImproperlyConfigured("depends_on must be a string or iterable of strings")
        return tuple(str(item) for item in value)
