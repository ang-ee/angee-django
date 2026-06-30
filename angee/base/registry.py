"""Resolve a registry key against a settings-backed key→dotted-path mapping.

The one owner of "turn a short key into its implementation class" — the rule both
:class:`~angee.base.fields.ImplClassField` (a per-row column) and the row-less
per-deployment selectors (the platform ``AddonInstaller`` backend) share. A key is
looked up in a Django setting mapping keys to dotted import paths, ``import_string``-ed
from the **composed, trusted** settings (never row text), and checked against a base
class. Keeping the rule here means a single resolution/validation shape across every
registry-backed selector instead of a copy per call site.
"""

from __future__ import annotations

from collections.abc import Mapping

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def impl_registry(registry_setting: str) -> dict[str, str]:
    """Return the configured ``key → dotted path`` mapping for ``registry_setting``.

    Reads the named Django setting and coerces it to ``str → str``. Raises
    ``ImproperlyConfigured`` when the setting is present but not a mapping.
    """

    mapping = getattr(settings, registry_setting, {}) if registry_setting else {}
    if not isinstance(mapping, Mapping):
        raise ImproperlyConfigured(f"settings.{registry_setting} must be a mapping of key to dotted path.")
    return {str(key): str(value) for key, value in mapping.items()}


def resolve_impl_class(registry_setting: str, key: str, base_class: type) -> type:
    """Return the impl class ``registry_setting`` binds to ``key`` (a ``base_class`` subclass).

    Resolves the **composed, trusted** dotted path (never row text) and verifies the
    import is a ``base_class`` subclass, raising ``ImproperlyConfigured`` with the known
    keys on a miss and naming the offending path on a type mismatch.
    """

    registry = impl_registry(registry_setting)
    try:
        dotted = registry[key]
    except KeyError as error:
        known = ", ".join(sorted(registry)) or "none configured"
        raise ImproperlyConfigured(
            f"No impl for key {key!r} in settings.{registry_setting} (known: {known})."
        ) from error
    impl = import_string(dotted)
    if not (isinstance(base_class, type) and isinstance(impl, type) and issubclass(impl, base_class)):
        base_name = getattr(base_class, "__name__", base_class)
        raise ImproperlyConfigured(
            f"settings.{registry_setting}[{key!r}] = {dotted!r} is not a {base_name}."
        )
    return impl
