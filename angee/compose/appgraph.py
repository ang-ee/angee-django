"""Django AppConfig dependency resolution for composition."""

from __future__ import annotations

from collections.abc import Iterable

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from angee.addons import addon_manifest, order_app_dependencies


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
    - ``angee_root_declaration``: the exact string authored in ``INSTALLED_APPS``
      for a root, or ``None`` for a dependency. Runtime drift checks compare this
      with the same editable setting without losing AppConfig-path spelling.
    - ``angee_forced``: whether any other resolved app depends on this one — the
      composer's reading of "cannot be uninstalled" for addons another installed
      addon needs. Transitive: ``A→B→C`` forces both ``B`` and ``C``. A leaf
      consumer/host root nothing depends on is not forced.
    """

    def resolve(
        self,
        roots: Iterable[str | AppConfig],
        *,
        declared_roots: Iterable[str | AppConfig] | None = None,
    ) -> tuple[AppConfig, ...]:
        """Resolve runnable roots and their ``depends_on`` closure.

        ``declared_roots`` identifies project declarations before framework
        defaults were injected. Omit it when every runnable root is authored.
        """

        declared = None if declared_roots is None else {
            root.name if isinstance(root, AppConfig) else root for root in declared_roots
        }

        app_configs_by_name: dict[str, AppConfig] = {}
        aliases: dict[str, str] = {}
        dependencies_by_name: dict[str, tuple[str, ...]] = {}
        root_names: list[str] = []
        root_name_set: set[str] = set()
        root_declarations: dict[str, str] = {}
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
            for dependency in sorted(dependencies_by_name[config.name]):
                dependency_name = aliases.get(dependency, dependency)
                dependency_config = app_configs_by_name.get(dependency_name)
                if dependency_config is None:
                    dependency_config = create_app_config(dependency_name, owner=config)
                    dependency_config = app_configs_by_name.get(dependency_config.name) or register(dependency_config)
                aliases[dependency] = dependency_config.name
                include_dependencies(dependency_config)

        for root in roots:
            config = root if isinstance(root, AppConfig) else create_app_config(aliases.get(root, root))
            declaration = config.name if isinstance(root, AppConfig) else root
            root_name = register(config).name
            root_names.append(root_name)
            if declared is None or declaration in declared:
                root_name_set.add(root_name)
                root_declarations[root_name] = declaration

        for name in root_names:
            include_dependencies(app_configs_by_name[name])

        try:
            ordered_names = order_app_dependencies(root_names, dependencies_by_name, aliases=aliases)
        except RuntimeError as error:
            raise ImproperlyConfigured(str(error)) from error
        ordered = tuple(app_configs_by_name[name] for name in ordered_names)

        depended_upon: set[str] = set()
        for config in ordered:
            for dependency in dependencies_by_name[config.name]:
                depended_upon.add(aliases.get(dependency, dependency))
        for config in ordered:
            config.angee_addon_root = config.name in root_name_set
            config.angee_root_declaration = root_declarations.get(config.name)
            config.angee_forced = config.name in depended_upon
        return ordered
