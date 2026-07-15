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
    GRANT_KIND,
    EntryGraph,
    EntryKey,
    GrantGroup,
    LoadResult,
    ResourceEntry,
    ResourceGroup,
    ResourceRow,
    ValidationResult,
    resolve_model,
    resource_manifest_for,
)
from angee.resources.exceptions import ResourceLoadError
from angee.resources.grants import materialize_grant_groups
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
        row_groups, grant_groups = self._groups_for(selected_addons, tiers=tiers)
        self._check_xref_collisions(row_groups)
        self._import_groups(
            row_groups,
            grant_groups,
            dry_run=True,
            addon_aliases=self._addon_aliases(selected_addons),
        )
        return ValidationResult(
            checked_files=len(row_groups) + len(grant_groups),
            checked_rows=(
                sum(len(group.rows) for group in row_groups) + sum(len(group.rows) for group in grant_groups)
            ),
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
        row_groups, grant_groups = self._groups_for(selected_addons, tiers=active_tiers)
        self._check_xref_collisions(row_groups)
        return self._import_groups(
            row_groups,
            grant_groups,
            dry_run=dry_run,
            addon_aliases=self._addon_aliases(selected_addons),
        )

    def _import_groups(
        self,
        row_groups: tuple[ResourceGroup, ...],
        grant_groups: tuple[GrantGroup, ...],
        *,
        dry_run: bool,
        addon_aliases: Mapping[str, str],
    ) -> LoadResult:
        """Import model rows and materialize grants; optionally roll back.

        Grants resolve their references through the ledger written by the row
        import above, so they run last within the same transaction. A dry run
        exercises both paths — resolving grant xrefs against the (uncommitted)
        rows — before rolling everything back.
        """

        load_result = LoadResult(created=0, updated=0, skipped=0)
        try:
            reason = "resources.validate" if dry_run else "resources.load"
            with system_context(reason=reason), transaction.atomic():
                loaded_groups: list[tuple[ResourceGroup, Any]] = []
                for group in row_groups:
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
                    loaded_groups.append((group, resource))
                if not dry_run:
                    self._run_post_load_hooks(loaded_groups)
                created, skipped = materialize_grant_groups(
                    grant_groups,
                    ledger_model=self.model,
                    addon_aliases=addon_aliases,
                )
                load_result = LoadResult(
                    created=load_result.created + created,
                    updated=load_result.updated,
                    skipped=load_result.skipped + skipped,
                )
                if dry_run:
                    raise DryRunRollback()
        except DryRunRollback:
            pass
        return load_result

    def _run_post_load_hooks(
        self,
        loaded_groups: list[tuple[ResourceGroup, Any]],
    ) -> None:
        """Dispatch model-owned hooks after all selected resource rows load."""

        for group, resource in loaded_groups:
            hook = getattr(group.model, "after_resource_load", None)
            if not callable(hook):
                continue
            instances_by_pk: dict[Any, models.Model] = {}
            for row in group.rows:
                instance = resource.instance_for_xref(row.xref)
                if instance is not None:
                    instances_by_pk[instance.pk] = instance
            if instances_by_pk:
                hook(
                    tuple(instances_by_pk.values()),
                    tier=group.entry.tier,
                    source=group.entry.source,
                    publish=group.entry.publish,
                )

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
            (
                entry.display,
                len(entry.read_grant_rows() if entry.kind == GRANT_KIND else entry.read_resource_rows()),
            )
            for entry in self._entries_for(addons, tiers=tiers)
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
    ) -> tuple[tuple[ResourceGroup, ...], tuple[GrantGroup, ...]]:
        """Return selected model-row groups and grant groups in dependency order."""

        groups: list[ResourceGroup] = []
        grant_groups: list[GrantGroup] = []
        by_key: dict[tuple[str, str, str], ResourceGroup] = {}
        for entry in self._entries_for(addons, tiers=tiers):
            if entry.kind == GRANT_KIND:
                grant_groups.append(GrantGroup(entry=entry, rows=entry.read_grant_rows()))
                continue
            for row in entry.read_resource_rows():
                model = resolve_model(row.model_label)
                key = (entry.addon.name, entry.source, model._meta.label_lower)
                group = by_key.get(key)
                if group is None:
                    group = ResourceGroup(entry=entry, model=model, rows=[])
                    by_key[key] = group
                    groups.append(group)
                group.rows.append(row)
        return tuple(groups), tuple(grant_groups)

    def _entries_for(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None,
    ) -> tuple[ResourceEntry, ...]:
        """Return selected resource entries in dependency order."""

        active_tiers = self._normalize_tiers(tiers)
        excluded = self._excluded_entry_keys()
        entries: list[ResourceEntry] = []
        for addon in addons:
            manifest = resource_manifest_for(addon)
            for tier in active_tiers:
                for declaration in manifest.get(tier, ()):
                    entry = ResourceEntry.from_declaration(
                        addon,
                        tier,
                        declaration,
                    )
                    if entry.key not in excluded:
                        entries.append(entry)
        return EntryGraph.from_entries(entries).ordered()

    def _excluded_entry_keys(self) -> frozenset[EntryKey]:
        """Return project-excluded resource entry keys from settings."""

        raw = getattr(settings, "ANGEE_RESOURCE_EXCLUDED_ENTRIES", ())
        if raw is None:
            return frozenset()
        if isinstance(raw, str) or not isinstance(raw, Iterable):
            raise ImproperlyConfigured(
                "ANGEE_RESOURCE_EXCLUDED_ENTRIES must be an iterable of 'addon:source' strings."
            )
        keys: list[EntryKey] = []
        for item in raw:
            if not isinstance(item, str):
                raise ImproperlyConfigured(
                    "ANGEE_RESOURCE_EXCLUDED_ENTRIES must contain only 'addon:source' strings."
                )
            addon, separator, source = item.partition(":")
            if not separator or not addon or not source:
                raise ImproperlyConfigured(
                    "ANGEE_RESOURCE_EXCLUDED_ENTRIES entries must use 'addon:source' strings."
                )
            keys.append((addon, source))
        return frozenset(keys)

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


setattr(ResourceQuerySet._entries_for, "queryset_only", False)

ResourceManager = AngeeUnscopedManager.from_queryset(ResourceQuerySet)
"""Manager exposing resource ledger operations."""
