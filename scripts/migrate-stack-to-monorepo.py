#!/usr/bin/env python3
"""Migrate a rendered stack's manifest from the split-repo roster to the monorepo.

Surgical by design (P8 corrections #7): ONLY the four donor framework sources and
the `framework` local path are touched; every custom source (docs, forks, private
work-state, bridges, arp) is preserved verbatim. `angee stack update --template`
cannot do this: its overlay merge retains old source keys and never rewrites
workspaces.

Usage:
    migrate-stack-to-monorepo.py <stack-root> [--repo URL] [--ref REF] [--apply]

Without --apply it prints the would-be manifest diff and exits nonzero if the
stack needs migration (dry-run). With --apply it backs up angee.yaml to
angee.yaml.pre-p8 and writes the migrated manifest, then fixes the `templates`
symlink and the stack answers file.

AFTER the manifest migration (documented, not automated here):
  1. `angee stack update --template --overwrite` re-renders pyproject/settings/
     pnpm-workspace from the monorepo templates (the bootstrap-projected addon
     dependency group is re-created by django's next start).
  2. Re-cut each workspaces/src-template workspace (destroy + create) once its
     slots are clean and pushed — the destroy guard enforces that.
  3. `angee dev` from the root.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import yaml

DONOR_SOURCES = ("angee-django", "angee-react", "angee-base", "angee-templates", "angee-examples")
OLD_FRAMEWORK_PATH = "workspaces/src/angee-django"
NEW_FRAMEWORK_PATH = "workspaces/src/angee"


def migrate(stack: dict, repo: str, ref: str) -> tuple[dict, list[str]]:
    notes: list[str] = []
    sources = stack.get("sources") or {}

    removed = [name for name in DONOR_SOURCES if name in sources]
    for name in removed:
        del sources[name]
    if removed:
        notes.append(f"removed donor sources: {', '.join(removed)}")

    if "angee" not in sources:
        sources["angee"] = {
            "kind": "git",
            "repo": repo,
            "default_ref": ref,
            "cache_path": "sources/angee",
        }
        notes.append(f"added source angee ({repo} @ {ref})")

    framework = sources.get("framework")
    if framework and framework.get("path") == OLD_FRAMEWORK_PATH:
        framework["path"] = NEW_FRAMEWORK_PATH
        notes.append(f"framework local path -> {NEW_FRAMEWORK_PATH}")

    kept = [n for n in sources if n not in ("angee", "framework", "app")]
    notes.append(f"preserved sources: {', '.join(sorted(kept)) or '(none)'}")

    for ws_name, ws in (stack.get("workspaces") or {}).items():
        if ws.get("template") == "workspaces/src":
            inputs = ws.setdefault("inputs", {})
            if "angee_django_ref" in inputs:
                inputs["angee_ref"] = inputs.pop("angee_django_ref")
                notes.append(f"workspace {ws_name}: angee_django_ref -> angee_ref")
            notes.append(
                f"workspace {ws_name}: NEEDS RE-CUT after slots are clean+pushed "
                "(destroy + create; the destroy guard protects unpushed work)"
            )
    return stack, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--repo", default="https://github.com/ang-ee/angee-django.git")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    manifest_path = args.root / "angee.yaml"
    before = manifest_path.read_text()
    stack = yaml.safe_load(before)
    migrated, notes = migrate(stack, args.repo, args.ref)
    after = yaml.safe_dump(migrated, sort_keys=False, default_flow_style=False)

    for note in notes:
        print(f"  - {note}")
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), "angee.yaml", "angee.yaml (migrated)", lineterm=""))
    if not any(n.startswith(("removed", "added", "framework", "workspace ")) for n in notes):
        print("nothing to migrate")
        return 0
    if not args.apply:
        print("\n".join(diff[:80]))
        print("\nDRY RUN — rerun with --apply to write. Also required at apply time:")
        print("  templates symlink -> sources/angee/templates; answers framework_path fix")
        return 1

    (args.root / "angee.yaml.pre-p8").write_text(before)
    manifest_path.write_text(after)

    link = args.root / "templates"
    if link.is_symlink():
        link.unlink()
        link.symlink_to("sources/angee/templates")
        print("templates symlink -> sources/angee/templates")

    answers = args.root / ".copier-answers.stack.yml"
    if answers.exists():
        text = answers.read_text().replace(OLD_FRAMEWORK_PATH, NEW_FRAMEWORK_PATH)
        answers.write_text(text)
        print("answers framework_path updated")
    print("APPLIED. Next: angee stack update --template --overwrite; re-cut src workspaces; angee dev")
    return 0


if __name__ == "__main__":
    sys.exit(main())
