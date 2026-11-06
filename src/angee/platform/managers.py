"""Platform catalogue reflection, change planning and native table reconciliation."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from django.apps import AppConfig, apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError, transaction
from django_compose.components import AppSelection, InstalledComponents, inspect_apps, installed_components
from rebac import database_context, system_context

from angee.base.serialization import canonical_json_sha256
from angee.iam.managers import ProtectedManager
from angee.platform import composed
from angee.platform.discovery import (
    available_addons,
    installed_entry_points,
    package_identity_index,
    requirement_addons,
)
from angee.platform.installer import (
    AddonInstaller,
    InstallResult,
    StaleAddonPreviewError,
    addon_installer,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from angee.platform.models import Addon


@dataclass(frozen=True, slots=True)
class AddonChangeImpact:
    """An addon entering or leaving the composed dependency graph."""

    name: str
    label: str
    root: bool
    depends_on: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AddonModelInventory:
    """Current rows visible to the administrator, not a deletion forecast."""

    label: str
    verbose_name: str
    row_count: int | None


@dataclass(frozen=True, slots=True)
class AddonContributedFieldInventory:
    """A loaded field supplied by this addon to another addon's concrete model."""

    model_label: str
    field_name: str
    verbose_name: str


@dataclass(frozen=True, slots=True)
class AddonDataInventory:
    """Loaded model inventory for one addon leaving the graph."""

    addon: str
    models: tuple[AddonModelInventory, ...]
    contributed_fields: tuple[AddonContributedFieldInventory, ...]


@dataclass(frozen=True, slots=True)
class AddonChangePreview:
    """A settings edit and its graph effects, bound to the reviewed snapshot."""

    action: str
    addon: str
    revision: str
    can_apply: bool
    refusal: str | None
    roots_before: tuple[str, ...]
    roots_after: tuple[str, ...]
    addons_to_enable: tuple[AddonChangeImpact, ...]
    addons_to_disable: tuple[AddonChangeImpact, ...]
    data_inventory: tuple[AddonDataInventory, ...]
    migration_warning: str | None
    settings_text: str = field(default="", repr=False)


class AddonManager[T: Addon](ProtectedManager[T]):
    """Manager owning reflection, authored roots, and native dependency jobs."""

    def enable(self, name: str, revision: str | None = None) -> InstallResult:
        """Validate and queue an addon root through the same plan the preview shows."""

        return self._apply_change(name, "enable", revision)

    def disable(self, name: str, revision: str | None = None) -> InstallResult:
        """Queue root removal, refusing an addon the running graph still requires."""

        return self._apply_change(name, "disable", revision)

    def install(self, name: str) -> dict[str, Any]:
        """Queue the native held dependency job; activation is a separate root edit."""

        return self._dependency_change(name, remove=False)

    def uninstall(self, name: str) -> dict[str, Any]:
        """Delegate package removal admission and execution to the native job owner."""

        return self._dependency_change(name, remove=True)

    def _dependency_change(self, name: str, *, remove: bool) -> dict[str, Any]:
        distribution = self.only("distribution").get(name=name).distribution
        if not distribution:
            raise ValidationError(f"{name} has no native distribution identity.")
        return addon_installer().backend.queue_dependency_change(distribution, remove=remove)

    def _apply_change(self, name: str, action: str, revision: str | None) -> InstallResult:
        """Validate one plan, write its exact roots, then reconcile the committed intent."""

        with database_context(self.db), system_context(reason=f"platform.addon.{action}"):
            installer = addon_installer()
            preview = self.change_preview(name, action, installer=installer)
            if not preview.can_apply or (revision is not None and preview.revision != revision):
                return InstallResult.refusal(
                    name, action, preview.refusal or "The addon preview is stale; review the changes again."
                )
            try:
                installer.apply_app_names(preview.roots_after, expected_text=preview.settings_text)
            except (OSError, NotImplementedError, StaleAddonPreviewError) as error:
                return InstallResult.refusal(name, action, str(error))
            reconciliation_pending = False
            try:
                self.reconcile_from_registry(desired=frozenset(preview.roots_after))
            except DatabaseError:
                # Settings are the committed source of truth. A derived mirror
                # failure cannot truthfully report that the edit was refused.
                logger.exception("Saved addon roots; catalogue reconciliation remains pending.")
                reconciliation_pending = True
            return InstallResult(
                name=name, action=action, already=preview.roots_before == preview.roots_after,
                reconciliation_pending=reconciliation_pending,
            )

    def change_preview(
        self,
        name: str,
        action: str,
        *,
        installer: AddonInstaller | None = None,
    ) -> AddonChangePreview:
        """Validate proposed roots in isolation and project their loaded-data impact."""

        if action not in {"enable", "disable"}:
            raise ValueError(f"Unknown addon change action {action!r}")
        bound_installer = installer or addon_installer()
        snapshot = bound_installer.installed_apps_snapshot()
        if snapshot is None:
            return AddonChangePreview(
                action=action,
                addon=name,
                revision="",
                can_apply=False,
                refusal="INSTALLED_APPS is not controlled by a readable project settings.yaml.",
                roots_before=(),
                roots_after=(),
                addons_to_enable=(),
                addons_to_disable=(),
                data_inventory=(),
                migration_warning=None,
            )
        index = installed_components("angee.addons")
        available = available_addons(index=index)
        packages = tuple(package for package, _origin in available.values())
        loaded_configs = {config.name: config for config in composed.addons()}
        roots_before = snapshot.names
        roots_after = roots_before
        refusal: str | None = None
        selection: AppSelection | None = None
        if action == "enable" and name not in available:
            refusal = f"{name} is not installed and cannot be enabled."
        else:
            try:
                aliases, _configs = self._app_identity(index=index)
                declarations = tuple(root for root in roots_before if aliases.get(root, root) == name)
                current: AppSelection | None = None
                if not declarations:
                    current = inspect_apps(roots_before, group="angee.addons")
                    aliases.update({
                        alias: entry["name"] for entry in current["app_entries"]
                        for alias in (entry["name"], entry["entry"], entry["config"])
                    })
                    declarations = tuple(root for root in roots_before if aliases.get(root, root) == name)
                if action == "enable":
                    roots_after = roots_before if declarations else (*roots_before, name)
                else:
                    refusal = self.disable_block_reason(name, loaded=loaded_configs, declared=bool(declarations))
                    if refusal is None:
                        roots_after = tuple(root for root in roots_before if root not in declarations)
                if refusal is None:
                    selection = (
                        current if current is not None and roots_after == roots_before
                        else inspect_apps(roots_after, group="angee.addons")
                    )
            except ImproperlyConfigured as error:
                refusal = str(error)
        loaded_names = set(loaded_configs)
        target_names = loaded_names if selection is None else set(selection["components"].values())
        enabled = () if selection is None else tuple(
            AddonChangeImpact(
                name=entry["name"],
                label=entry["label"],
                root=entry["name"] in selection["roots"],
                depends_on=tuple(selection["dependencies"][entry["name"]]),
            )
            for entry in sorted(selection["app_entries"], key=lambda entry: entry["name"])
            if entry["name"] in target_names - loaded_names
        )
        closure = getattr(settings, "COMPOSE_CLOSURE", None)
        dependencies = {} if closure is None else closure.dependencies
        loaded_roots = composed.root_app_names()
        loaded = tuple(
            AddonChangeImpact(
                name=name,
                label=loaded_configs[name].label,
                root=name in loaded_roots,
                depends_on=tuple(dependencies.get(name, ())),
            )
            for name in sorted(loaded_names)
        )
        disabled = tuple(impact for impact in loaded if impact.name not in target_names)
        inventory = self._data_inventory((impact.name for impact in disabled))
        warning = (
            "Disabling addons may require schema migrations that remove model data or contributed fields. "
            "Review and author migration history before provisioning; "
            "provision refuses schema drift without authored migration history."
            if disabled else None
        )
        revision = _preview_revision(
            snapshot.text, packages, action=action, addon=name, roots_after=roots_after,
            selection=selection, loaded=loaded,
        )
        return AddonChangePreview(
            action=action,
            addon=name,
            revision=revision,
            can_apply=refusal is None,
            refusal=refusal,
            roots_before=roots_before,
            roots_after=roots_after,
            addons_to_enable=enabled,
            addons_to_disable=disabled,
            data_inventory=inventory,
            migration_warning=warning,
            settings_text=snapshot.text,
        )

    @staticmethod
    def _dependants(manifests: Iterable[Mapping[str, Any]], *, aliases: Mapping[str, str]) -> dict[str, list[str]]:
        """Invert the caller's declaration set using canonical dependency names."""

        depended_by: dict[str, list[str]] = {}
        for manifest in sorted(manifests, key=lambda item: item["name"]):
            for dependency in sorted({aliases.get(name, name) for name in manifest["depends_on"]}):
                depended_by.setdefault(dependency, []).append(manifest["name"])
        return depended_by

    def disable_block_reason(
        self,
        name: str,
        *,
        loaded: Mapping[str, AppConfig],
        declared: bool,
    ) -> str | None:
        """Refuse loaded dependencies or unknown targets, ignoring stale catalogue flags."""

        config = loaded.get(name)
        if config is not None and name not in composed.root_app_names():
            closure = getattr(settings, "COMPOSE_CLOSURE", None)
            dependencies = {} if closure is None else closure.dependencies
            required_by = ", ".join(sorted(
                app for app, required in dependencies.items() if name in required
            )) or "another enabled app"
            return f"{name} is required by {required_by} and cannot be disabled."
        if name not in loaded and not declared and not self.filter(name=name).exists():
            return f"{name} is not known to this project."
        return None

    @staticmethod
    def _app_identity(
        *, index: InstalledComponents | None = None,
    ) -> tuple[dict[str, str], dict[str, AppConfig]]:
        """Read only loaded identity and installed entry-point aliases."""

        configs = {config.name: config for config in apps.get_app_configs()}
        aliases = {
            key: config.name for config in configs.values()
            for key in (config.name, config.label, f"{type(config).__module__}.{type(config).__qualname__}")
        }
        aliases.update(composed.root_app_aliases())
        aliases.update({entry.value: name for name, entry in installed_entry_points(index=index).items()})
        return aliases, configs

    @staticmethod
    def _data_inventory(names: Iterable[str]) -> tuple[AddonDataInventory, ...]:
        configs = {config.name: config for config in composed.addons()}
        inventories = []
        for name in sorted(names):
            config = configs.get(name)
            if config is None:
                continue
            models_inventory = []
            for model in composed.data_models(config):
                try:
                    count = model._default_manager.count()
                except DatabaseError:
                    count = None
                models_inventory.append(
                    AddonModelInventory(model._meta.label_lower, str(model._meta.verbose_name_plural), count)
                )
            contributed = tuple(
                AddonContributedFieldInventory(row.model_label, row.field_name, row.verbose_name)
                for row in composed.contributed_fields(config)
            )
            inventories.append(AddonDataInventory(name, tuple(models_inventory), contributed))
        return tuple(inventories)

    def reconcile_loaded_registry(self) -> None:
        """Converge after startup using the effective roots recorded by the native snapshot."""

        self.reconcile_from_registry(desired=composed.root_app_names())

    def reconcile_from_registry(self, *, desired: frozenset[str] | None) -> None:
        """Converge the table to the composed app graph + available addons.

        A **state** reconcile, never a delete: an addon that leaves the project is
        marked ``REMOVED`` — its row, and so its history, is kept — rather than
        pruned. Scoped to the tier this reconcile owns (installed/local); rows of
        other tiers (the VCS marketplace ``platform_integrate_vcs`` contributes) are
        left untouched. Each present addon's row is a full overwrite so a state flip
        (enabled ↔ disabled) resets every reflected field. Runs under the caller's
        ``system_context`` (see ``signals.py``), wrapped in one transaction like
        the sibling source reconciles, so a mid-loop failure never leaves the
        table half-converged.

        ``desired`` contains authored settings roots. Available roots awaiting
        composition and loaded roots removed from that desired set are pending;
        loaded dependencies never queue a disable merely by being absent from the
        roots. When desired settings are unreadable (``None``), retain each row's
        pending flag. The shared dependency reconcile includes live local/remote
        declarations and excludes removed declarers.
        """

        index = installed_components("angee.addons")
        available = available_addons(index=index)
        loaded = {config.name: config for config in composed.addons()}
        aliases, configs = self._app_identity(index=index)
        configs.update(loaded)
        canonical_desired = (
            None
            if desired is None
            else frozenset(aliases.get(declaration, declaration) for declaration in desired)
        )
        manifests = {name: manifest for name, (manifest, _origin) in available.items()}
        counts = composed.resource_counts()
        rows = self.all()
        with database_context(self.db), transaction.atomic(using=self.db):
            package_identity_index((
                *((row.distribution, row.name) for row in rows.select_for_update() if row.distribution),
                *((package["distribution"], name) for name, package in manifests.items()),
            ))
            pending_by_name = dict(rows.values_list("name", "pending")) if canonical_desired is None else {}
            rows.filter(source__in=(self.model.AddonSource.INSTALLED, self.model.AddonSource.LOCAL)).exclude(
                name__in=manifests
            ).update(
                state=self.model.AddonState.REMOVED,
                **self.model.reset_runtime_facts(),
            )
            for name, manifest in sorted(manifests.items()):
                config = configs.get(name)
                enabled = name in loaded
                root = enabled and name in composed.root_app_names()
                runtime_facts = self.model.reset_runtime_facts()
                if enabled:
                    data_models = composed.data_models(loaded[name])
                    runtime_facts.update(
                        forced=not root,
                        model_count=len(data_models),
                        field_count=sum(len(composed.own_fields(model)) for model in data_models),
                        resource_count=counts.get(name, 0),
                        model_labels=sorted(model._meta.label_lower for model in data_models),
                    )
                if canonical_desired is None:
                    pending = bool(pending_by_name.get(name, False))
                else:
                    pending = (root and name not in canonical_desired) if enabled else name in canonical_desired
                rows.update_or_create(
                    name=name,
                    defaults={
                        **runtime_facts,
                        "label": config.label if config is not None else "",
                        "namespace": manifest["namespace"],
                        "distribution": manifest["distribution"],
                        "requirements": list(manifest["requirements"]),
                        "description": manifest["description"],
                        "keywords": list(manifest["keywords"]),
                        "category": manifest["category"] or "",
                        "kind": self.model.AddonKind.CONSUMER if root else self.model.AddonKind.REQUIRED,
                        "source": self.model.AddonSource.INSTALLED,
                        "state": self.model.AddonState.ENABLED if enabled else self.model.AddonState.DISABLED,
                        "pending": pending,
                        "depends_on": list(manifest["depends_on"]),
                    },
                )

            self.reconcile_dependencies(index=index)

    def reconcile_dependencies(self, *, index: InstalledComponents | None = None) -> None:
        """Project loaded compiler edges and catalogue requirements, then reverse them.

        A newly discovered package can resolve requirements retained from an older
        source refresh. Removed declarers keep historical forward edges but do
        not contribute outgoing edges. Rows predating native metadata retain their
        snapshot until their source or installed distribution refreshes.
        """

        index = installed_components("angee.addons") if index is None else index
        closure = getattr(settings, "COMPOSE_CLOSURE", None)
        dependencies = {} if closure is None else closure.dependencies
        with database_context(self.db), transaction.atomic(using=self.db):
            rows = list(self.select_for_update().order_by("name"))
            identities = package_identity_index((
                *((entry.dist.metadata["Name"], name) for name, entry in installed_entry_points(index=index).items()
                  if entry.dist is not None),
                *((row.distribution, row.name) for row in rows if row.distribution),
            ))
            live = [row for row in rows if row.state != self.model.AddonState.REMOVED]
            forward_changes = []
            for row in live:
                if row.name in dependencies:
                    projected = list(dependencies[row.name])
                elif row.distribution:
                    projected = list(requirement_addons(row.requirements, identities))
                else:
                    continue
                if row.depends_on != projected:
                    row.depends_on = projected
                    forward_changes.append(row)
            self.bulk_update(forward_changes, ["depends_on"])
            aliases, _configs = self._app_identity(index=index)
            incoming = self._dependants(
                ({"name": row.name, "depends_on": row.depends_on} for row in live), aliases=aliases,
            )
            reverse_changes = []
            for row in rows:
                value = incoming.get(row.name, [])
                if row.depended_by != value:
                    row.depended_by = value
                    reverse_changes.append(row)
            self.bulk_update(reverse_changes, ["depended_by"])

    def pending_changes(self) -> bool | None:
        """Return verified desired-vs-loaded drift, or ``None`` when settings are unreadable.

        This cheap status path compares root names only; catalogue metadata and
        resource counts stay in reconciliation. The project YAML is read only when
        the composition owner confirms it is the effective source of
        ``INSTALLED_APPS``; overridden settings return an honest unknown state.
        """

        if "INSTALLED_APPS" not in getattr(settings, "ANGEE_PROJECT_YAML_SETTINGS", ()):
            return None
        loaded = composed.root_app_names()
        desired = addon_installer().desired_app_names()
        aliases = composed.root_app_aliases()
        return None if desired is None else frozenset(aliases.get(root, root) for root in desired) != loaded


def _preview_revision(
    text: str,
    manifests: Iterable[Mapping[str, Any]],
    *,
    action: str,
    addon: str,
    roots_after: tuple[str, ...],
    selection: AppSelection | None = None,
    loaded: Iterable[AddonChangeImpact] = (),
) -> str:
    """Bind the decision to settings, package facts, and loaded/proposed app graphs."""

    payload = [
        {
            "name": manifest["name"],
            "distribution": manifest["distribution"],
            "requirements": list(manifest["requirements"]),
            "depends_on": list(manifest["depends_on"]),
            "description": manifest["description"],
            "category": manifest["category"],
        }
        for manifest in sorted(manifests, key=lambda item: item["name"])
    ]
    decision = {
        "settings": text,
        "manifests": payload,
        "action": action,
        "addon": addon,
        "roots_after": roots_after,
        "selection": selection,
        "loaded": [asdict(impact) for impact in sorted(loaded, key=lambda impact: impact.name)],
    }
    return canonical_json_sha256(decision)
