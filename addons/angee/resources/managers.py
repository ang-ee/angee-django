"""QuerySet and manager APIs for the resource ledger model."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import ExitStack
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, IntegrityError, models, router, transaction
from import_export.exceptions import ImportError as ResourceImportError
from rebac import system_context
from rebac.models import active_relationship_model

from angee.base.models import AngeeUnscopedManager, AngeeUnscopedQuerySet
from angee.resources.entries import (
    GRANT_KIND,
    EntryGraph,
    EntryKey,
    GrantGroup,
    LoadResult,
    ResourceEntry,
    ResourceGroup,
    ValidationResult,
    resource_manifest_for,
)
from angee.resources.exceptions import ResourceLoadError
from angee.resources.grants import materialize_grant_groups
from angee.resources.loader import (
    DryRunRollback,
    build_resource,
)
from angee.resources.mixins import ResourceWritePreparation


class ResourceQuerySet(AngeeUnscopedQuerySet[Any]):
    """Reusable filtered reads over resource-ledger rows."""

    def counts_by_addon(self) -> dict[str, int]:
        """Return ledger row counts keyed by source addon (the dotted name)."""

        with system_context(reason="resources.counts_by_addon"):
            return {
                row["source_addon"]: row["count"]
                for row in self.values("source_addon").annotate(count=models.Count("id"))
            }

    def ledger_page(self, *, limit: int) -> list[Any]:
        """Return up to ``limit`` ledger rows in the queryset's declared order."""

        with system_context(reason="resources.ledger_page"):
            return list(self.all()[:limit])


class ResourceManager(AngeeUnscopedManager.from_queryset(ResourceQuerySet)):  # type: ignore[misc]
    """Orchestrate selected addon resources independently of ledger row filters."""

    def validate_addons(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None = None,
    ) -> ValidationResult:
        """Validate selected addon resource files without saving rows."""

        selected_addons = tuple(addons)
        entries, row_groups, grant_groups = self._groups_for(selected_addons, tiers=tiers)
        self._check_xref_collisions(row_groups)
        self._import_groups(
            entries,
            row_groups,
            grant_groups,
            dry_run=True,
            addon_aliases=self._addon_aliases(selected_addons),
        )
        return ValidationResult(
            checked_files=len({group.entry.key for group in row_groups} | {group.entry.key for group in grant_groups}),
            checked_rows=(
                sum(len(group.dataset) for group in row_groups) + sum(len(group.rows) for group in grant_groups)
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
        entries, row_groups, grant_groups = self._groups_for(selected_addons, tiers=active_tiers)
        self._check_xref_collisions(row_groups)
        return self._import_groups(
            entries,
            row_groups,
            grant_groups,
            dry_run=dry_run,
            addon_aliases=self._addon_aliases(selected_addons),
        )

    def _import_groups(
        self,
        entries: tuple[ResourceEntry, ...],
        row_groups: tuple[ResourceGroup, ...],
        grant_groups: tuple[GrantGroup, ...],
        *,
        dry_run: bool,
        addon_aliases: Mapping[str, str],
    ) -> LoadResult:
        """Import model rows and materialize grants; optionally roll back.

        Independent grants follow model rows as before. Explicit dependencies
        take precedence, so a row that validates a membership may depend on its
        grant. Post-load hooks observe both rows and grants. Dry runs execute
        row validation and grant writes inside the same rollback boundary.
        """

        loaded_groups = [
            (
                group,
                build_resource(group.model, group.entry, ledger_model=self.model, addon_aliases=addon_aliases),
            )
            for group in row_groups
        ]
        write_models = [self.model, *(group.model for group in row_groups)]
        write_models.extend(
            field.remote_field.through
            for group in row_groups
            for field in group.model._meta.many_to_many
            if field.name in group.dataset.headers
        )
        if grant_groups:
            write_models.append(active_relationship_model())
        aliases = {
            self.db,
            *(router.db_for_write(model) for model in write_models),
            *(resource.get_db_connection_name() for _group, resource in loaded_groups),
        }
        if aliases != {DEFAULT_DB_ALIAS}:
            raise ResourceLoadError(
                "Resource rows, ledger and grants must use the default database to share the resource load transaction."
            )
        rows_by_entry: dict[EntryKey, list[tuple[ResourceGroup, Any]]] = defaultdict(list)
        for group, resource in loaded_groups:
            rows_by_entry[group.entry.key].append((group, resource))
        grants_by_entry = {group.entry.key: group for group in grant_groups}
        load_result = LoadResult(created=0, updated=0, skipped=0)
        try:
            reason = "resources.validate" if dry_run else "resources.load"
            with system_context(reason=reason), transaction.atomic():
                with ExitStack() as preparations:
                    for plan in self._write_preparations(loaded_groups):
                        preparations.enter_context(plan.owner.prepare_resource_writes(plan.targets))
                    imported_groups: list[tuple[ResourceGroup, Any]] = []
                    for entry in entries:
                        if entry.kind == GRANT_KIND:
                            created, skipped = materialize_grant_groups(
                                (grants_by_entry[entry.key],),
                                ledger_model=self.model,
                                addon_aliases=addon_aliases,
                            )
                            load_result = LoadResult(
                                created=load_result.created + created,
                                updated=load_result.updated,
                                skipped=load_result.skipped + skipped,
                            )
                            continue
                        for group, resource in rows_by_entry[entry.key]:
                            try:
                                result = resource.import_data(
                                    group.dataset,
                                    dry_run=False,
                                    raise_errors=True,
                                    rollback_on_validation_errors=True,
                                    use_transactions=False,
                                )
                            except ResourceImportError as error:
                                if error.number is not None:
                                    error.number = group.source_rows[error.number - 1]
                                raise ResourceLoadError(f"{group.entry.display}: {error}") from error
                            except IntegrityError as error:
                                raise ResourceLoadError(f"{group.entry.display}: {error}") from error
                            load_result = load_result.with_result(result)
                            imported_groups.append((group, resource))
                    if not dry_run:
                        self._run_post_load_hooks(imported_groups)
                    if dry_run:
                        raise DryRunRollback()
        except DryRunRollback:
            pass
        return load_result

    @staticmethod
    def _write_preparations(
        loaded_groups: list[tuple[ResourceGroup, Any]],
    ) -> tuple[ResourceWritePreparation, ...]:
        """Merge model-declared targets so each owner prepares once in stable order."""

        grouped: dict[str, tuple[Any, set[Any]]] = {}
        for group, resource in loaded_groups:
            hook = getattr(group.model, "resource_write_preparation", None)
            plan = hook(resource, group.dataset) if callable(hook) else None
            if plan is None:
                continue
            key = plan.owner._meta.label_lower
            owner, targets = grouped.setdefault(key, (plan.owner, set()))
            targets.update(plan.targets)
        return tuple(
            ResourceWritePreparation(owner=owner, targets=frozenset(targets))
            for _key, (owner, targets) in sorted(grouped.items())
        )

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
            for xref in group.dataset["_xref"]:
                instance = resource.instance_for_xref(xref)
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
                len(entry.read_grant_rows())
                if entry.kind == GRANT_KIND
                else sum(len(group.dataset) for group in entry.read_groups()),
            )
            for entry in self._entries_for(addons, tiers=tiers)
        )

    def _groups_for(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None,
    ) -> tuple[tuple[ResourceEntry, ...], tuple[ResourceGroup, ...], tuple[GrantGroup, ...]]:
        """Return selected entries and their model-row/grant groups, including empty entries."""

        groups: list[ResourceGroup] = []
        grant_groups: list[GrantGroup] = []
        entries = self._entries_for(addons, tiers=tiers)
        for entry in entries:
            if entry.kind == GRANT_KIND:
                grant_groups.append(GrantGroup(entry=entry, rows=entry.read_grant_rows()))
            else:
                groups.extend(entry.read_groups())
        return entries, tuple(groups), tuple(grant_groups)

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
        return EntryGraph.from_entries(sorted(entries, key=lambda entry: entry.kind == GRANT_KIND)).ordered()

    def _excluded_entry_keys(self) -> frozenset[EntryKey]:
        """Return project-excluded resource entry keys from settings."""

        raw = getattr(settings, "ANGEE_RESOURCE_EXCLUDED_ENTRIES", ())
        if raw is None:
            return frozenset()
        if isinstance(raw, str) or not isinstance(raw, Iterable):
            raise ImproperlyConfigured("ANGEE_RESOURCE_EXCLUDED_ENTRIES must be an iterable of 'addon:source' strings.")
        keys: list[EntryKey] = []
        for item in raw:
            if not isinstance(item, str):
                raise ImproperlyConfigured("ANGEE_RESOURCE_EXCLUDED_ENTRIES must contain only 'addon:source' strings.")
            addon, separator, source = item.partition(":")
            if not separator or not addon or not source:
                raise ImproperlyConfigured("ANGEE_RESOURCE_EXCLUDED_ENTRIES entries must use 'addon:source' strings.")
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

        seen: dict[tuple[str, str], ResourceEntry] = {}
        for group in groups:
            for xref in group.dataset["_xref"]:
                key = (group.entry.addon.name, xref)
                previous = seen.get(key)
                if previous is not None:
                    raise ResourceLoadError(
                        f"xref collision in {group.entry.addon.name}: "
                        f"{xref!r} appears in {previous.display} "
                        f"and {group.entry.display}"
                    )
                seen[key] = group.entry
