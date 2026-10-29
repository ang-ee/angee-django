"""Installed addon facts and catalogue presentation vocabulary."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import metadata
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
from django.db.models import TextChoices

from angee.addons import addon_entry_points, facts_from_distribution


class AddonCategory(TextChoices):
    """Catalogue group vocabulary shared by installed and remote package facts."""

    ARP = "ARP"
    AGENTS = "Agents"
    DATA = "Data"
    EXAMPLE = "Example"
    FOUNDATION = "Foundation"
    INTEGRATION = "Integration"
    PHROGG = "Phrogg"
    PLATFORM = "Platform"
    PRODUCTIVITY = "Productivity"
    USER_INTERFACE = "User interface"
    WORKFLOWS = "Workflows"


def catalogue_category(value: object) -> str:
    """Validate a package's optional catalogue grouping at its capability owner."""

    if value == "":
        return ""
    try:
        return AddonCategory(value)
    except ValueError as error:
        raise ImproperlyConfigured(f"Unknown tool.angee.category {value!r}; expected {AddonCategory.values}") from error


def available_addons() -> dict[str, tuple[Mapping[str, Any], metadata.EntryPoint]]:
    """Return installed package facts and native entry points without importing apps.

    Editable installs carry the same distribution identity as wheels. Uninstalled
    source projects belong to the VCS catalogue, rather than a second filesystem
    availability or activation path. The shared package owner reads RECORD or
    PEP 610 and projects native metadata.
    """

    available = {}
    for name, entry_point in sorted(addon_entry_points().items()):
        facts = facts_from_distribution(cast(metadata.Distribution, entry_point.dist))
        facts["category"] = catalogue_category(facts["category"])
        available[name] = (facts, entry_point)
    return available

