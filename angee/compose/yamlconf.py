"""Native django-yamlconf merge, error and declaration-provenance helpers.

The host's ProjectContract owns project files and environment conventions.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import django_yamlconf
from django.core.exceptions import ImproperlyConfigured

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


def setting_sources(settings: object, name: str) -> tuple[str, ...]:
    """Return effective and merged declaration owners from native provenance."""

    info = django_yamlconf.get_attr_info(name, settings)
    if not info:
        return ()
    sources = (info.get("source"), *(source for _value, source in info.get("history", ())))
    return tuple(dict.fromkeys(source for source in sources if isinstance(source, str)))


def is_setting_name(name: str) -> bool:
    """Return whether ``name`` is a top-level Django setting (public, all-caps).

    The one owner of the rule that decides which namespace entries the composer
    treats as Django settings. The ``YAMLCONF_ATTRIBUTES`` provenance sentinel
    is exported alongside settings but is not itself a setting name, so callers
    that carry it forward OR it in explicitly.
    """

    return not name.startswith("_") and name.isupper()
