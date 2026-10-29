"""Project package metadata into the marketplace catalogue descriptor."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from typing import Any

from angee.addons import facts_from_toml
from angee.platform.discovery import catalogue_category


def parse_addon_meta(blob: bytes, *, dependencies: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Project an addon pyproject; ordinary projects contribute no catalogue row."""

    facts = facts_from_toml(tomllib.loads(blob.decode("utf-8")), dependencies=dependencies)
    if not facts:
        return {}
    return {
        **facts,
        "category": catalogue_category(facts["category"]),
        "namespace": facts["name"].split(".", 1)[0],
        "keywords": list(facts["keywords"]),
        "depends_on": list(facts["depends_on"]),
    }
