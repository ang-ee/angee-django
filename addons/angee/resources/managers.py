"""QuerySet and manager APIs for the resource ledger model."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, models, transaction
from import_export.exceptions import ImportError as ResourceImportError
from rebac import system_context

from angee.base.models import AngeeUnscopedManager, AngeeUnscopedQuerySet
from angee.resources.entries import (
    EntryGraph,
    LoadResult,
    ResourceEntry,
    ResourceGroup,
    ResourceRow,
    ValidationResult,
    resolve_model,
    resource_manifest_for,
)
from angee.resources.exceptions import ResourceLoadError
from angee.resources.loader import (
    DryRunRollback,
    build_resource,
)


class ResourceQuerySet(AngeeUnscopedQuerySet[Any]):
    """QuerySet methods for validating, loading, and diffing resources."""

    def validate_addons(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None = None,
    ) -> ValidationResult:
        """Validate selected addon resource files without saving rows."""

        selected_addons = tuple(addons)
        groups = self._groups_for(selected_addons, tiers=tiers)
        self._check_xref_collisions(groups)
        self._import_groups(
            groups,
            dry_run=True,
            addon_aliases=self._addon_aliases(selected_addons),
        )
        return ValidationResult(
            checked_files=len(groups),
            checked_rows=sum(len(group.rows) for group in groups),
        )

    def load_addons(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object],
        allow_non_dev: bool = False,
        dry_run: bool = False,
    ) -> LoadResult:
        """Load selected addon resource tiers idempotently."""

        active_tiers = self._normalize_tiers(tiers)
        if self.model.Tier.DEMO in active_tiers and not (settings.DEBUG or allow_non_dev):
            raise ImproperlyConfigured("resources load demo requires DEBUG or --allow-non-dev")

        selected_addons = tuple(addons)
        groups = self._groups_for(selected_addons, tiers=active_tiers)
        self._check_xref_collisions(groups)
        return self._import_groups(
            groups,
            dry_run=dry_run,
            addon_aliases=self._addon_aliases(selected_addons),
        )

    def _import_groups(
        self,
        groups: tuple[ResourceGroup, ...],
        *,
        dry_run: bool,
        addon_aliases: Mapping[str, str],
    ) -> LoadResult:
        """Import ``groups`` and optionally roll the transaction back."""

        load_result = LoadResult(created=0, updated=0, skipped=0)
        try:
            reason = "resources.validate" if dry_run else "resources.load"
            with system_context(reason=reason), transaction.atomic():
                for group in groups:
                    resource = build_resource(
                        group.model,
                        group.entry,
                        ledger_model=self.model,
                        addon_aliases=addon_aliases,
                    )
                    try:
                        result = resource.import_data(
                            group.to_dataset(),
                            dry_run=False,
                            raise_errors=True,
                            rollback_on_validation_errors=True,
                            use_transactions=False,
                        )
                    except (IntegrityError, ResourceImportError) as error:
                        raise ResourceLoadError(f"{group.entry.display}: {error}") from error
                    load_result = load_result.with_result(result)
                if dry_run:
                    raise DryRunRollback()
        except DryRunRollback:
            pass
        return load_result

    def _addon_aliases(self, addons: Iterable[Any]) -> dict[str, str]:
        """Return app names and labels mapped to canonical app names."""

        aliases: dict[str, str] = {}
        for addon in addons:
            for alias in (addon.name, addon.label):
                existing = aliases.setdefault(alias, addon.name)
                if existing != addon.name:
                    raise ImproperlyConfigured(f"Duplicate addon alias {alias!r}")
        return aliases

    def diff_addons(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None = None,
    ) -> tuple[tuple[str, int], ...]:
        """Return resource display names and parsed row counts."""

        return tuple(
            (entry.display, len(entry.read_resource_rows())) for entry in self._entries_for(addons, tiers=tiers)
        )

    def counts_by_addon(self) -> dict[str, int]:
        """Return ledger row counts keyed by source addon (the dotted name).

        The ledger is untyped (no ``rebac_resource_type``); the elevated read is
        bracketed in ``system_context`` so it is explicit and audited. Callers own
        the access gate (e.g. the platform console's ``platform/explorer`` read).
        """

        with system_context(reason="resources.counts_by_addon"):
            return {
                row["source_addon"]: row["count"]
                for row in self.values("source_addon").annotate(count=models.Count("id"))
            }

    def ledger_page(self, *, limit: int) -> list[Any]:
        """Return up to ``limit`` ledger rows in the model's declared order.

        A hard ``limit`` bounds the fetch and the GraphQL payload — the ledger
        grows one row per imported resource. The read is elevated/audited as in
        ``counts_by_addon``; callers own the access gate.
        """

        with system_context(reason="resources.ledger_page"):
            return list(self.all()[:limit])

    def _groups_for(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None,
    ) -> tuple[ResourceGroup, ...]:
        """Return selected rows grouped by source entry and model."""

        groups: list[ResourceGroup] = []
        by_key: dict[tuple[str, str, str], ResourceGroup] = {}
        for entry in self._entries_for(addons, tiers=tiers):
            for row in entry.read_resource_rows():
                model = resolve_model(row.model_label)
                key = (entry.addon.name, entry.source, model._meta.label_lower)
                group = by_key.get(key)
                if group is None:
                    group = ResourceGroup(entry=entry, model=model, rows=[])
                    by_key[key] = group
                    groups.append(group)
                group.rows.append(row)
        return tuple(groups)

    def _entries_for(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None,
    ) -> tuple[ResourceEntry, ...]:
        """Return selected resource entries in dependency order."""

        active_tiers = self._normalize_tiers(tiers)
        entries: list[ResourceEntry] = []
        for addon in addons:
            manifest = resource_manifest_for(addon)
            for tier in active_tiers:
                for declaration in manifest.get(tier, ()):
                    entries.append(
                        ResourceEntry.from_declaration(
                            addon,
                            tier,
                            declaration,
                        )
                    )
        return EntryGraph.from_entries(entries).ordered()

    def _normalize_tiers(
        self,
        tiers: Iterable[object] | None,
    ) -> tuple[str, ...]:
        """Return normalized tier values with prerequisite tiers included."""

        return self.model.Tier.with_prerequisites(tiers)

    def _check_xref_collisions(
        self,
        groups: tuple[ResourceGroup, ...],
    ) -> None:
        """Raise when an addon declares the same xref more than once."""

        seen: dict[tuple[str, str], ResourceRow] = {}
        for group in groups:
            for row in group.rows:
                key = (group.entry.addon.name, row.xref)
                previous = seen.get(key)
                if previous is not None:
                    raise ResourceLoadError(
                        f"xref collision in {group.entry.addon.name}: "
                        f"{row.xref!r} appears in {previous.entry.display} "
                        f"and {group.entry.display}"
                    )
                seen[key] = row


ResourceManager = AngeeUnscopedManager.from_queryset(ResourceQuerySet)
"""Manager exposing resource ledger operations."""
