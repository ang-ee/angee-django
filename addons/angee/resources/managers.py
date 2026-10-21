"""QuerySet and manager APIs for the resource ledger model."""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Self, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, models, transaction
from import_export.exceptions import ImportError as ResourceImportError
from rebac import system_context

from angee.changesets import changeset, current_changeset
from angee.fields import ModelLabelField
from angee.identity import SqidPublicIdentity, public_id_of
from angee.impl import resolve_impl_class
from angee.jsonschema import relation_positions
from angee.models import AngeeModel, AngeeUnscopedManager, AngeeUnscopedQuerySet, SqidMixin
from angee.resources.entries import (
    GRANT_KIND,
    ROW_KIND,
    EntryGraph,
    EntryKey,
    GrantGroup,
    LoadResult,
    ResourceEntry,
    ResourceGroup,
    ValidationResult,
    resource_manifest_for,
    retired_models_for,
)
from angee.resources.exceptions import ResourceLoadError
from angee.resources.grants import materialize_grant_groups
from angee.resources.kinds import ResourceEntryKind, ResourceKindDeclarations
from angee.resources.loader import (
    DryRunRollback,
    build_resource,
)
from angee.resources.mixins import ResourceLoadMixin
from angee.resources.widgets import is_xref, resolve_xref, split_xref

if TYPE_CHECKING:
    from angee.resources.models import Resource


class ResourceQuerySet[LedgerT: Resource](AngeeUnscopedQuerySet[LedgerT]):
    """Reusable filtered reads over resource-ledger rows."""

    def counts_by_addon(self) -> dict[str, int]:
        """Return ledger row counts keyed by source addon (the dotted name)."""

        with system_context(reason="resources.counts_by_addon"):
            return {
                row["source_addon"]: row["count"]
                for row in self.values("source_addon").annotate(count=models.Count("id"))
            }

    def ledger_page(self, *, limit: int) -> list[LedgerT]:
        """Return up to ``limit`` ledger rows in the queryset's declared order."""

        with system_context(reason="resources.ledger_page"):
            return list(self.all()[:limit])

    def seeding(self, record: models.Model) -> Self:
        """Return the ledger rows whose target is ``record``, by its model label and public id."""

        return self.filter(target_model=record._meta.label, target_id=public_id_of(record))

    def seeded_target_keys(self, model_label: str, *, source_addons: Iterable[str]) -> set[Any]:
        """Return the primary keys of ``model_label`` rows ``source_addons``' resource files seeded.

        The model's code may already be gone, so a ledger's public id decodes without
        it: a sqid id through :class:`SqidPublicIdentity` under its own prefix, kept
        only when it re-encodes to itself; any other id is the row's primary key as
        text (:func:`public_id_of`). Callers convert keys to the model's key type.
        """

        sqid = SqidMixin._meta.get_field("sqid")
        public_ids = list(
            self.filter(target_model__iexact=model_label, source_addon__in=list(source_addons))
            .exclude(target_id="")
            .values_list("target_id", flat=True)
        )
        keys: set[Any] = set()
        for public_id in public_ids:
            prefix, separator, _encoded = public_id.partition("_")
            if separator:
                identity = SqidPublicIdentity(prefix=f"{prefix}_", min_length=sqid.min_length, alphabet=sqid.alphabet)
                value = identity.public_id_to_pk(public_id)
                if value is not None and identity.public_id_from_pk(value) == public_id:
                    keys.add(value)
                    continue
            keys.add(public_id)
        return keys

    def retired_seed_keys(self, model_label: str) -> set[Any]:
        """Return the keys of ``model_label`` rows seeded by the installed addons that retire the model.

        Those addons name it under ``retired_models`` in their addon.toml
        ``[resources]`` (:func:`retired_models_for`); with none, no row is seeded.
        """

        label = model_label.lower()
        owners = [
            config.name for config in apps.get_app_configs()
            if label in {retired.lower() for retired in retired_models_for(config)}
        ]
        return self.seeded_target_keys(model_label, source_addons=owners) if owners else set()


def seeded_record_merge_guard(record: models.Model) -> str | None:
    """Refuse merging away a record a resource ledger loaded (an ``ANGEE_MERGE_GUARDS`` entry).

    The next load would find no row for its xref and create the record again.
    """

    with system_context(reason="resources.seeded_record_merge_guard"):
        ledger = apps.get_model("resources", "Resource").objects.seeding(record).first()
    if ledger is None:
        return None
    return f"{record} is loaded from {ledger.source_addon} resources, which would recreate it; keep it instead."


_ResourceManagerBase = AngeeUnscopedManager.from_queryset(ResourceQuerySet)


class ResourceManager[LedgerT: Resource](_ResourceManagerBase[LedgerT]):
    """Orchestrate selected addon resources independently of ledger row filters."""

    def get_queryset(self) -> ResourceQuerySet[LedgerT]:
        """Retain the configured ledger queryset and concrete row type."""

        return cast(ResourceQuerySet[LedgerT], super().get_queryset())

    def resolve(self, handle: str, *, model: type[models.Model] | None = None) -> models.Model:
        """Resolve a ledger handle under the current actor, optionally checking its type."""

        return resolve_xref(handle, self.model, self._addon_aliases(apps.get_app_configs()), model=model)

    def resolve_config_references(self, value: Any, *, schema: dict[str, Any]) -> Any:
        """Resolve dotted xrefs only at schema-declared relation positions.

        Public ids and ordinary strings remain literal. The ledger resolution
        owner enforces target types, as it does for resource imports.
        """

        resolved = copy.deepcopy(value)
        for path, relation, item in relation_positions(schema, value):
            public_id = self.resolve_config_reference(item, relation=relation)
            if not path:
                resolved = public_id
                continue
            parent = resolved
            for part in path[:-1]:
                parent = parent[part]
            parent[path[-1]] = public_id
        return resolved

    def resolve_config_reference(self, value: str, *, relation: dict[str, Any]) -> str:
        """Resolve one declared relation's xref, preserving literal public ids."""

        if not is_xref(value):
            return value
        target = self.resolve(value, model=ModelLabelField.get_model(relation["resource"]))
        return str(public_id_of(target))

    def bind_instance(
        self, *, addon: Any, xref: str, instance: models.Model, source: str, replace: bool = False,
    ) -> None:
        """Use the import ledger for owner-created installation identities."""

        if instance.pk is None:
            raise ResourceLoadError("Installation identities require a saved target.")
        with transaction.atomic():
            resource = build_resource(
                type(instance), ResourceEntry(addon=addon, tier=self.model.Tier.INSTALL, source_value=source),
                ledger_model=self.model, addon_aliases=self._addon_aliases(apps.get_app_configs()),
            )
            resource.bind_instance(xref, instance, replace=replace)

    def validate_addons(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None = None,
    ) -> ValidationResult:
        """Validate selected addon resource files without saving rows."""

        selected_addons = tuple(addons)
        entries, row_groups, grant_groups, kind_declarations = self._groups_for(
            selected_addons, tiers=tiers,
        )
        self._check_xref_collisions(row_groups)
        self._import_groups(
            entries,
            row_groups,
            grant_groups,
            kind_declarations,
            dry_run=True,
            addon_aliases=self._addon_aliases(selected_addons),
        )
        return ValidationResult(
            checked_files=len(entries),
            checked_rows=(
                sum(len(group.dataset) for group in row_groups) + sum(len(group.rows) for group in grant_groups)
                + sum(len(rows) for rows in kind_declarations.values())
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
        self._check_load_tiers(active_tiers, allow_non_dev=allow_non_dev)

        selected_addons = tuple(addons)
        entries, row_groups, grant_groups, kind_declarations = self._groups_for(
            selected_addons, tiers=active_tiers,
        )
        self._check_xref_collisions(row_groups)
        return self._import_groups(
            entries,
            row_groups,
            grant_groups,
            kind_declarations,
            dry_run=dry_run,
            addon_aliases=self._addon_aliases(selected_addons),
        )

    def load_xref[M: AngeeModel](
        self,
        handle: str,
        *,
        model: type[M],
        actor: Any = None,
        allow_non_dev: bool = False,
    ) -> M:
        """Import one declared ``addon.name.xref`` through its model's resource.

        Other rows, grants and prerequisite entries are not imported; their
        targets must already exist. Native widgets, hooks and the ledger still
        apply. The caller needs write access before updating an existing target
        and after import, including on unchanged replays. Newly imported targets
        also require create permission before the import transaction can commit.
        """
        installed = tuple(apps.get_app_configs())
        aliases = self._addon_aliases(installed)
        addon_name, xref = split_xref(handle, aliases)
        addon = next(addon for addon in installed if addon.name == addon_name)
        groups = tuple(
            group
            for entry in self._declared_entries_for((addon,), tiers=None)
            if entry.kind == ROW_KIND
            for group in entry.read_groups()
        )
        self._check_xref_collisions(groups)
        for group in groups:
            for index, candidate in enumerate(group.dataset["_xref"]):
                if candidate != xref:
                    continue
                if group.model is not model:
                    raise ResourceLoadError(
                        f"Resource {handle!r} names {group.model._meta.label}, expected {model._meta.label}."
                    )
                self._check_load_tiers((group.entry.tier,), allow_non_dev=allow_non_dev)
                selected = ResourceGroup(
                    group.entry,
                    group.model_label,
                    group.dataset.subset(rows=[index]),
                    [group.source_rows[index]],
                )
                with transaction.atomic():
                    resource = build_resource(model, group.entry, ledger_model=self.model, addon_aliases=aliases)
                    with system_context(reason="resources.load_xref resolve existing target"):
                        resolved, _ = resource.resolve_existing(((selected.dataset, resource),))
                    resolution = resolved.get((resource, xref))
                    if resolution is not None and resolution.instance is not None:
                        if not isinstance(resolution.instance, model):
                            raise ResourceLoadError(f"Resource {handle!r} did not produce {model._meta.label}.")
                        actor = resolution.instance.require_access("write", actor)
                    self._import_groups(
                        (group.entry,),
                        (selected,),
                        (),
                        None,
                        dry_run=False,
                        addon_aliases=aliases,
                        actor=actor,
                    )
                    with system_context(reason="resources.load_xref resolve imported target"):
                        instance = resolve_xref(handle, self.model, aliases)
                    if not isinstance(instance, model):
                        raise ResourceLoadError(f"Resource {handle!r} did not produce {model._meta.label}.")
                    if resolution is None or resolution.instance is None:
                        actor = instance.require_access("create", actor)
                    instance.require_access("write", actor)
                    return instance
        raise LookupError(f"Resource {handle!r} was not found.")

    def _check_load_tiers(self, tiers: Iterable[object], *, allow_non_dev: bool) -> None:
        if self.model.Tier.DEMO in tiers and not (settings.DEBUG or allow_non_dev):
            raise ImproperlyConfigured("resources load demo requires DEBUG or --allow-non-dev")

    def _import_groups(
        self,
        entries: tuple[ResourceEntry, ...],
        row_groups: tuple[ResourceGroup, ...],
        grant_groups: tuple[GrantGroup, ...],
        kind_declarations: ResourceKindDeclarations | None = None,
        *,
        dry_run: bool,
        addon_aliases: Mapping[str, str],
        actor: Any = None,
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
                build_resource(
                    group.model,
                    group.entry,
                    ledger_model=self.model,
                    addon_aliases=addon_aliases,
                    kind_declarations=kind_declarations,
                ),
            )
            for group in row_groups
        ]
        rows_by_entry: dict[EntryKey, list[tuple[ResourceGroup, Any]]] = defaultdict(list)
        for group, resource in loaded_groups:
            rows_by_entry[group.entry.key].append((group, resource))
        grants_by_entry = {group.entry.key: group for group in grant_groups}
        load_result = LoadResult(created=0, updated=0, skipped=0)
        try:
            reason = "resources.validate" if dry_run else "resources.load"
            with (
                system_context(reason=reason),
                changeset(fresh=True, activity="seed"),
                transaction.atomic(),
            ):
                current_changeset(record=True)
                resource_classes = {
                    group.model.resource_class
                    for group, _ in loaded_groups
                    if issubclass(group.model, ResourceLoadMixin) and group.model.resource_class is not None
                }
                for resource_class in sorted(resource_classes, key=lambda cls: (cls.__module__, cls.__qualname__)):
                    resource_class.lock_imports(loaded_groups)
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
                    if entry.kind != ROW_KIND:
                        continue
                    for group, resource in rows_by_entry[entry.key]:
                        try:
                            result = resource.import_data(
                                group.dataset,
                                dry_run=False,
                                raise_errors=True,
                                rollback_on_validation_errors=True,
                                use_transactions=False,
                                actor=actor,
                            )
                        except ResourceImportError as error:
                            if error.number is not None:
                                error.number = group.source_rows[error.number - 1]
                            raise ResourceLoadError(f"{group.entry.display}: {error}") from error
                        except IntegrityError as error:
                            raise ResourceLoadError(f"{group.entry.display}: {error}") from error
                        load_result = load_result.with_result(result)
                if not dry_run:
                    self._run_post_load_hooks(loaded_groups)
                for kind, declarations in (kind_declarations or {}).items():
                    resolve_impl_class(ResourceEntryKind, kind).after_load(
                        declarations,
                        loaded_groups=loaded_groups,
                        dry_run=dry_run,
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
            for xref in group.dataset["_xref"]:
                instance = resource.instance_for_xref(xref)
                if instance is not None:
                    instances_by_pk[instance.pk] = instance
            if instances_by_pk:
                hook(
                    tuple(instances_by_pk.values()),
                    tier=group.entry.tier,
                    source=group.entry.source,
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
                else sum(len(group.dataset) for group in entry.read_groups())
                if entry.kind == ROW_KIND
                else len(resolve_impl_class(ResourceEntryKind, entry.kind).read(entry)),
            )
            for entry in self._entries_for(addons, tiers=tiers)
        )

    def _groups_for(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None,
    ) -> tuple[
        tuple[ResourceEntry, ...],
        tuple[ResourceGroup, ...],
        tuple[GrantGroup, ...],
        ResourceKindDeclarations,
    ]:
        """Return selected entries with parsed native and owner-defined groups."""

        groups: list[ResourceGroup] = []
        grant_groups: list[GrantGroup] = []
        declarations: dict[str, list[Any]] = defaultdict(list)
        entries = self._entries_for(addons, tiers=tiers)
        for entry in entries:
            if entry.kind == GRANT_KIND:
                grant_groups.append(GrantGroup(entry=entry, rows=entry.read_grant_rows()))
            elif entry.kind == ROW_KIND:
                groups.extend(entry.read_groups())
            else:
                declarations[entry.kind].extend(resolve_impl_class(ResourceEntryKind, entry.kind).read(entry))
        return entries, tuple(groups), tuple(grant_groups), {
            kind: tuple(rows) for kind, rows in declarations.items()
        }

    def _entries_for(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None,
    ) -> tuple[ResourceEntry, ...]:
        """Return selected resource entries in dependency order."""

        entries = self._declared_entries_for(addons, tiers=tiers)
        return EntryGraph.from_entries(sorted(entries, key=lambda entry: entry.kind == GRANT_KIND)).ordered()

    def _declared_entries_for(
        self,
        addons: Iterable[Any],
        *,
        tiers: Iterable[object] | None,
    ) -> tuple[ResourceEntry, ...]:
        """Enumerate selected declarations without requiring a complete dependency graph."""

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
        return tuple(entries)

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
