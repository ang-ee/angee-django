"""Bounded integration with django-yamlconf's native settings pipeline.

Only this adapter knows yamlconf's provenance internals. Project loading excludes
implicit ancestor files; addon overlays use its native attribute merge semantics.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import django_yamlconf
from django.core.exceptions import ImproperlyConfigured

from angee.paths import resolve_path
from angee.project import PROJECT_YAML_NAME

YAMLCONF_PREDEFINED_SETTINGS = frozenset(
    {
        "CPU_COUNT",
        "OS_MACHINE",
        "OS_NODE",
        "OS_PROCESSOR",
        "OS_RELEASE",
        "OS_SYSTEM",
        "PYTHON",
        "TOP_DIR",
        "USER",
        "VIRTUAL_ENV",
    }
)


YAMLCONF_ATTRIBUTES = "_YAMLCONF_ATTRIBUTES"
YAMLCONF_INTERNAL_SOURCE = "**INTERNAL**"
YAMLCONF_ENVIRONMENT_SOURCE = "**ENVIRONMENT**"


class _YamlconfErrorHandler(logging.Handler):
    """Turn yamlconf logged errors into composition failures."""

    def emit(self, record: logging.LogRecord) -> None:
        """Raise for every yamlconf error record."""

        raise ImproperlyConfigured(record.getMessage())


@contextmanager
def fail_on_yamlconf_errors() -> Iterator[None]:
    """Raise ``ImproperlyConfigured`` when django-yamlconf logs an error."""

    logger = logging.getLogger("django_yamlconf")
    handler = _YamlconfErrorHandler(level=logging.ERROR)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)


def setting_name(attribute_name: str) -> str:
    """Return the top-level Django setting name for one yamlconf attribute."""

    return attribute_name.split(":", maxsplit=1)[0].split(".", maxsplit=1)[0]


def is_setting_name(name: str) -> bool:
    """Return whether ``name`` is a top-level Django setting (public, all-caps).

    The one owner of the rule that decides which namespace entries the composer
    treats as Django settings. The ``YAMLCONF_ATTRIBUTES`` provenance sentinel
    is exported alongside settings but is not itself a setting name, so callers
    that carry it forward OR it in explicitly.
    """

    return not name.startswith("_") and name.isupper()


def load_project(
    project_settings: ModuleType,
    root: Path,
) -> None:
    """Apply the project's YAML and environment settings overlay.

    A bounded reproduction of ``django_yamlconf.load``. That entry point
    walks from the project root up to the filesystem root (``dirtree_find``)
    collecting every ``settings.yaml`` it finds, so hosting this project
    inside another Angee instance — whose own ``settings.yaml`` is an
    ancestor of every nested project — silently loads and *overrides* the
    project with the host's values. Composing the pipeline from yamlconf's
    module-level functions bounds the sources to exactly the project's own
    ``<root>/settings.yaml`` plus the explicit ``YAMLCONF_CONFFILE`` overlay,
    preserving load order (project file, then ``YAMLCONF_CONFFILE``, then
    the environment) and the ``BASE_DIR``/``TOP_DIR`` bootstrap.
    """

    with fail_on_yamlconf_errors():
        loader, loader_kwargs = django_yamlconf.get_loader("yaml")
        if loader is None:
            return
        attributes = django_yamlconf.bootstrap_attributes(str(root))
        project_yaml = root / f"{PROJECT_YAML_NAME}.yaml"
        if os.access(project_yaml, os.R_OK):
            django_yamlconf.load_conffile(attributes, project_settings, loader, loader_kwargs, str(project_yaml))
        if final_conf := os.environ.get("YAMLCONF_CONFFILE"):
            django_yamlconf.load_conffile(attributes, project_settings, loader, loader_kwargs, final_conf)
        django_yamlconf.load_envdefs(attributes, project_settings)
        django_yamlconf.expand_attribute_refs(attributes)
        django_yamlconf.inject_attr(attributes, project_settings)


def reject_unexpected_sources(
    project_settings: ModuleType,
    root: Path,
    settings_module: str,
) -> None:
    """Reject yamlconf's implicit ancestor ``settings.yaml`` cascade."""

    allowed_sources = {
        YAMLCONF_INTERNAL_SOURCE,
        YAMLCONF_ENVIRONMENT_SOURCE,
        settings_module,
    }
    project_yaml = (root / "settings.yaml").resolve()
    if project_yaml.exists():
        allowed_sources.add(str(project_yaml))
    if final_conf := os.environ.get("YAMLCONF_CONFFILE"):
        allowed_sources.add(str(resolve_path(final_conf)))

    for attribute in getattr(project_settings, YAMLCONF_ATTRIBUTES, {}).values():
        sources = [attribute.get("source"), *(source for _value, source in attribute.get("history", ()))]
        for source in sources:
            if source in allowed_sources:
                continue
            try:
                source_path = str(resolve_path(str(source)))
            except ImproperlyConfigured, OSError, TypeError, ValueError:
                source_path = str(source)
            if source_path not in allowed_sources:
                raise ImproperlyConfigured(f"Unexpected django-yamlconf source {source!r}")
