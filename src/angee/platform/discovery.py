"""Installed addon facts and catalogue presentation vocabulary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib import metadata
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db.models import TextChoices
from django_compose.components import InstalledComponents, active_requirements, installed_components
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from angee.base.addons import capability_facts, package_document


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

    if isinstance(value, str):
        if value == "":
            return ""
        try:
            return AddonCategory(value)
        except ValueError:
            pass
    raise ImproperlyConfigured(
        f"Unknown tool.angee.platform.category {value!r}; expected {AddonCategory.values}"
    )


def installed_entry_points(*, index: InstalledComponents | None = None) -> dict[str, metadata.EntryPoint]:
    """Project the native provider index into catalogue app identities."""

    index = installed_components("angee.addons") if index is None else index
    return {app: index.entry_points[provider] for app, provider in sorted(index.providers.items())}


def package_identity_index(identities: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Validate installed and retained catalogue ownership before database writes.

    Native installed metadata is already validated by django-compose. This owner
    also reconciles uninstalled source claims and permits equal installed/source
    pairs while refusing conflicting ownership across catalogue sources.
    """

    providers: dict[str, str] = {}
    apps: dict[str, str] = {}
    for distribution, app in identities:
        provider = canonicalize_name(distribution)
        previous_app = providers.setdefault(provider, app)
        previous_provider = apps.setdefault(app, provider)
        if previous_app != app or previous_provider != provider:
            raise ImproperlyConfigured(
                f"Conflicting catalogue identity: {distribution!r} / {app!r}; "
                f"existing {provider!r} / {previous_app!r}, provider {previous_provider!r}"
            )
    return providers


def _requirement_apps(requirements: Iterable[Requirement], identities: Mapping[str, str]) -> tuple[str, ...]:
    identities = package_identity_index(identities.items())
    return tuple(sorted({
        app for requirement in requirements
        if (app := identities.get(canonicalize_name(requirement.name))) is not None
    }))


def requirement_addons(requirements: Iterable[str], identities: Mapping[str, str]) -> tuple[str, ...]:
    """Resolve catalogue requirements through the native extra-free marker owner."""

    return _requirement_apps(active_requirements(requirements), identities)


def _category(document: Mapping[str, Any]) -> str:
    return catalogue_category(capability_facts(document, "angee.platform").get("category", ""))


def facts_from_distribution(
    distribution: metadata.Distribution, *, index: InstalledComponents | None = None,
    document: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project native metadata and capability facts without loading the addon.

    Source ingestion supplies its authored capability document while prepared
    dist-info owns identity and common metadata. Installed reads locate the
    packaged document through the distribution.
    """

    index = installed_components("angee.addons") if index is None else index
    provider = canonicalize_name(distribution.metadata["Name"])
    if provider not in index.entry_points:
        return {}
    point = index.entry_points[provider]
    if document is None:
        document = package_document(distribution, point.name)
    return {
        "name": point.name, "namespace": point.name.split(".", 1)[0], "distribution": provider,
        "requirements": tuple(distribution.requires or ()),
        "description": distribution.metadata.get("Summary", ""),
        "keywords": tuple(
            value.strip() for value in distribution.metadata.get("Keywords", "").split(",") if value.strip()
        ),
        "category": _category(document),
        "depends_on": _requirement_apps(
            index.active_requirements(provider), {provider: app for app, provider in index.providers.items()},
        ),
    }


def available_addons(
    *, index: InstalledComponents | None = None,
) -> dict[str, tuple[Mapping[str, Any], metadata.EntryPoint]]:
    """List installed addon facts without importing apps or loading entry points."""

    index = installed_components("angee.addons") if index is None else index
    return {
        app: (facts_from_distribution(index.distributions[provider], index=index), index.entry_points[provider])
        for app, provider in sorted(index.providers.items())
    }
