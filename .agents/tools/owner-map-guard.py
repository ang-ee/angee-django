#!/usr/bin/env python3
"""Advisory Claude Code ``PreToolUse`` hook for ``Edit|Write|MultiEdit``.

On the first source edit of a session under ``addons/``, ``packages/``,
``angee/`` or ``examples/`` it adds a reminder to run ``angee-design`` and write
the owner map before continuing. Paths are classified relative to the project
root (``$CLAUDE_PROJECT_DIR``, else the nearest ancestor of the event ``cwd``
holding ``AGENTS.md`` and ``.agents/``); a relative ``file_path`` resolves
against the event ``cwd``. Tests, migrations, Markdown and colocated
``*.test.*``/``*.spec.*``/``*.stories.*`` files are not source edits. It fires
once per session (marker file under ``$TMPDIR``), never blocks, and exits 0
silently on any malformed input. An agent that ignores instructions is caught by
review, not by this hook. Wired through ``.claude/settings.json``; stdlib only.
"""

import json
import os
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path

AREAS = ("addons", "packages", "angee", "examples")
COLOCATED = ("*.test.*", "*.spec.*", "*.stories.*")
REMINDER = (
    "Architecture Gate: this is the first source edit of the session.\n"
    "1. Run the angee-design skill (.agents/skills/angee-design/SKILL.md) first.\n"
    "2. Write the owner map and deletion list into the current note, then continue."
)


def project_root(cwd: Path) -> Path | None:
    """``$CLAUDE_PROJECT_DIR``, else the nearest ancestor of ``cwd`` that holds ``AGENTS.md`` and ``.agents/``."""
    configured = os.environ.get("CLAUDE_PROJECT_DIR")
    if configured:
        return Path(configured).resolve()
    candidates = (cwd, *cwd.parents)
    return next((d for d in candidates if (d / "AGENTS.md").is_file() and (d / ".agents").is_dir()), None)


def is_source(relative: Path) -> bool:
    """Whether a project-relative path is production source under a guarded area."""
    parts = relative.parts
    if not parts or parts[0] not in AREAS or "tests" in parts or "migrations" in parts or relative.suffix == ".md":
        return False
    return not any(fnmatch(relative.name, pattern) for pattern in COLOCATED)


def main() -> None:
    payload = json.load(sys.stdin)
    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()
    root = project_root(cwd)
    path = (cwd / payload["tool_input"]["file_path"]).resolve()
    if root is None or not path.is_relative_to(root) or not is_source(path.relative_to(root)):
        return
    marker = Path(tempfile.gettempdir()) / f"angee-owner-map-{payload['session_id']}"
    if marker.exists():
        return
    marker.touch()
    output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": REMINDER}}
    json.dump(output, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - advisory: never fail or block the tool call
        pass
    sys.exit(0)
