"""Available addon manifests and native Django app identity for the marketplace."""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Iterable
from importlib import metadata
from pathlib import Path
from typing import cast

from django.apps import AppConfig, apps
from django.core.exceptions import ImproperlyConfigured
from hatch_angee import AddonManifest, ManifestError, discover, parse_manifest

ADDON_ENTRY_POINT_GROUP = "angee.addons"
logger = logging.getLogger(__name__)


def available_addons(
    addon_dirs: Iterable[Path | str] = (),
) -> dict[str, tuple[AddonManifest, metadata.EntryPoint | Path]]:
    """Return unchanged manifests and native discovery origins, keyed by name.

    Installed ``angee.addons`` entry points take precedence over local paths;
    local roots retain hatch-angee's caller-defined precedence. Duplicate
    installed names are ambiguous and rejected. Discovery does not populate
    Django's registry or invoke app lifecycle hooks.
    """

    available: dict[str, tuple[AddonManifest, metadata.EntryPoint | Path]] = {}
    for entry_point in sorted(
        metadata.entry_points(group=ADDON_ENTRY_POINT_GROUP), key=lambda entry: (entry.name, entry.value)
    ):
        if entry_point.name in available:
            previous = cast(metadata.EntryPoint, available[entry_point.name][1])
            raise ImproperlyConfigured(
                f"Duplicate installed addon entry point {entry_point.name!r}: "
                f"{previous.value!r} and {entry_point.value!r}"
            )
        try:
            spec = importlib.util.find_spec(entry_point.module)
        except (ImportError, AttributeError, ValueError) as error:
            raise ImproperlyConfigured(f"Cannot resolve addon entry point {entry_point.value!r}") from error
        roots = tuple(Path(path) for path in spec.submodule_search_locations or ()) if spec else ()
        if not roots and spec is not None and spec.origin:
            roots = (Path(spec.origin).parent,)
        markers = tuple(root / "addon.toml" for root in roots if (root / "addon.toml").is_file())
        if len(markers) != 1:
            raise ImproperlyConfigured(
                f"{entry_point.value}: expected one import-package addon.toml, found {len(markers)}"
            )
        marker = markers[0]
        try:
            manifest = parse_manifest(marker)
        except (ManifestError, OSError) as error:
            raise ImproperlyConfigured(str(error)) from error
        if manifest.name != entry_point.name:
            raise ImproperlyConfigured(
                f"{marker}: addon.name {manifest.name!r} disagrees with entry point {entry_point.name!r}"
            )
        available[entry_point.name] = (manifest, entry_point)
    for addon_dir, manifest in discover(addon_dirs):
        available.setdefault(manifest.name, (manifest, addon_dir))
    return dict(sorted(available.items()))


def resolve_app_config(declaration: str, *, expected_name: str | None = None) -> AppConfig | None:
    """Resolve native identity without enabling an available app.

    Reuse a populated config by its canonical name.
    Otherwise Django's factory preserves explicit config-class declarations and
    default-config selection. Import and configuration errors leave identity
    unknown and log the declaration; unexpected application errors propagate.
    Construction may import app packages and
    ``apps.py`` but never populates a registry or calls ``ready()``. A supplied
    manifest name must agree with the resolved native name.
    """

    config = None
    if apps.apps_ready:
        for candidate in apps.get_app_configs():
            if candidate.name == declaration:
                config = candidate
                break
    if config is None:
        try:
            config = AppConfig.create(declaration)
        except (ImportError, ImproperlyConfigured) as error:
            logger.warning("Cannot resolve addon app declaration %r: %s", declaration, error)
            return None
    if expected_name is not None and config.name != expected_name:
        raise ImproperlyConfigured(f"Addon name {expected_name!r} disagrees with AppConfig.name {config.name!r}")
    return config


