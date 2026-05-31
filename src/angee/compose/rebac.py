"""Permission schema rendering and syncing for composed runtimes."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from django.core.management import call_command

from angee.base.apps import BaseAddonConfig


def render_permissions(addons: Iterable[BaseAddonConfig]) -> str:
    """Return combined REBAC schema text for ``addons``."""

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
    return "\n\n".join(sections).rstrip() + "\n"


def write_permissions(
    runtime_dir: Path,
    addons: Iterable[BaseAddonConfig],
) -> Path:
    """Write the combined REBAC schema file into ``runtime_dir``."""

    target = runtime_dir / "permissions.zed"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_permissions(addons), encoding="utf-8")
    return target


def sync_permissions(*, check: bool = False) -> None:
    """Delegate permission loading to django-zed-rebac."""

    args = ["sync"]
    if check:
        args.append("--check")
    call_command("rebac", *args, verbosity=0)
