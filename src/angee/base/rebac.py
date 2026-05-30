"""Thin glue around django-zed-rebac package schemas."""

from __future__ import annotations

from pathlib import Path

from django.core.management import call_command

from angee.base.apps import BaseAddonConfig
from angee.base.discovery import discover_addons


def iter_permission_paths(
    addons: tuple[BaseAddonConfig, ...] | None = None,
) -> tuple[Path, ...]:
    """Return package permission files in build order."""

    discovered = discover_addons() if addons is None else addons
    return tuple(
        path
        for addon in discovered
        if (path := addon.get_rebac_schema_path()) is not None
    )


def write_permissions(
    runtime_dir: Path,
    addons: tuple[BaseAddonConfig, ...],
) -> Path:
    """Write a combined permission file for review and drift checks."""

    sections: list[str] = []
    for addon in addons:
        path = addon.get_rebac_schema_path()
        if path is None:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if text:
            sections.append(f"// addon: {addon.name}\n{text}")
    target = runtime_dir / "permissions.zed"
    target.write_text("\n\n".join(sections).rstrip() + "\n", encoding="utf-8")
    return target


def sync_permissions(*, check: bool = False) -> None:
    """Delegate permission schema loading to django-zed-rebac."""

    args = ["sync"]
    if check:
        args.append("--check")
    call_command("rebac", *args, verbosity=0)
