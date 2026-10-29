"""Error bridging and namespace selection for Angee’s native yamlconf handoff."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from django.core.exceptions import ImproperlyConfigured

YAMLCONF_ATTRIBUTES = "_YAMLCONF_ATTRIBUTES"


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


def is_setting_name(name: str) -> bool:
    """Return whether ``name`` is a top-level Django setting (public, all-caps).

    Angee forwards public settings to the native configure owner. The
    ``YAMLCONF_ATTRIBUTES`` provenance sentinel
    is exported alongside settings but is not itself a setting name, so callers
    that carry it forward OR it in explicitly.
    """

    return not name.startswith("_") and name.isupper()
