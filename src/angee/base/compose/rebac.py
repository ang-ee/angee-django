"""Thin glue around django-zed-rebac package schemas."""

from __future__ import annotations

from pathlib import Path

from django.core.management import call_command

from angee.base.apps import BaseAddonConfig


def write_permissions(
    runtime_dir: Path,
    addons: tuple[BaseAddonConfig, ...],
) -> Path:
    """Write a combined permission file for review and drift checks."""

    sections: list[str] = []
    seen: set[Path] = set()
    for addon in addons:
        path = addon.rebac_schema_path
        if path is None:
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
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
