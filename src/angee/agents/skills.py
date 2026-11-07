"""Parser for a skill manifest (``SKILL.md``).

A skill is a directory bearing a ``SKILL.md`` whose YAML frontmatter declares the
skill's ``name`` and ``description`` (the Agent Skills convention). Mirrors
``integrate.vcs.templates.parse_template_meta``: ``VcsBridge.discover`` does one
recursive walk over a source's subtree and fills in the bearing directory as the
``path``; this parser owns only the frontmatter.
"""

from __future__ import annotations

import json
from typing import Any

import yaml


def parse_skill_meta(blob: bytes) -> dict[str, Any]:
    """Return a skill descriptor parsed from one ``SKILL.md`` blob.

    Leftover frontmatter keys land in ``metadata``, coerced JSON-safe: YAML turns an
    unquoted ``key: 2024-01-15`` into a ``date``, which the model's default
    ``JSONField`` encoder cannot store — left raw it would abort the whole source sync.
    """

    front_matter = _front_matter(blob.decode("utf-8", errors="replace"))
    meta = yaml.safe_load(front_matter) if front_matter else {}
    if not isinstance(meta, dict):
        meta = {}
    reserved = {"name", "description"}
    metadata = {str(key): value for key, value in meta.items() if key not in reserved}
    return {
        "name": str(meta.get("name", "")),
        "description": str(meta.get("description", "")),
        "metadata": json.loads(json.dumps(metadata, default=str)),
    }


def _front_matter(text: str) -> str:
    """Return the YAML frontmatter block fenced by ``---`` lines, or empty."""

    if not text.startswith("---"):
        return ""
    lines = text.splitlines()
    closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if closing is None:
        return ""
    return "\n".join(lines[1:closing])
