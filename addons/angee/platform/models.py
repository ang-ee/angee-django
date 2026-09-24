"""Source models for the platform addon.

The platform console reflects the runtime the composer already built. ``Addon`` is
that reflection made persistent: one row per composed/available addon, **converged
from the app registry after migrate** — the same reconcile Django runs for
``django_content_type`` / ``auth_permission`` (see ``signals.py``). It is therefore
authoritatively *derived*, never authored: ``settings.yaml`` (enabled) and
``uv.lock`` (available) remain the source of truth; this table is the queryable
mirror that backs the console.

The registry reconcile owns installed/local availability and running composition.
The same ``Addon`` table also holds remote catalogue rows;
``platform_integrate_vcs`` supplies their declarations and provenance. Each source
reconcile retains removed rows as history.

``PlatformExplorer`` stays a table-less REBAC type anchor for the schema explorer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint
from typing import Any

from django.apps import AppConfig
from django.conf import settings
from django.db import DatabaseError, models, transaction
from hatch_angee import AddonManifest
from rebac import system_context

from angee.addons import addon_manifest, available_addons, resolve_app_config, resolve_manifest_roots
from angee.base.db import get_write_alias
from angee.base.fields import StateField
from angee.base.models import AngeeManager, AngeeModel
from angee.base.serialization import canonical_json_sha256
from angee.platform import composed
from angee.platform.installer import (
    AddonInstaller,
    InstallResult,
    LocalInstallerBackend,
    StaleAddonPreviewError,
    addon_installer,
)


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


class AddonManager(AngeeManager):
    """Manager owning reflection, change previews, and install/disable flow."""

    def install(self, name: str, revision: str | None = None) -> InstallResult:
        """Validate and queue an addon root through the same plan the preview shows."""

        return self._apply_change(name, "install", revision)

    def disable(self, name: str, revision: str | None = None) -> InstallResult:
        """Queue root removal, refusing an addon the running graph still requires."""

        return self._apply_change(name, "disable", revision)

    def _apply_change(self, name: str, action: str, revision: str | None) -> InstallResult:
        """Validate one plan, write its exact roots, then reconcile the committed intent."""

        using = get_write_alias(self.model, bound=self)
        with system_context(reason=f"platform.addon.{action}"):
            installer = addon_installer()
            preview = self.db_manager(using).change_preview(name, action, installer=installer)
            if not preview.can_apply or (revision is not None and preview.revision != revision):
                return InstallResult.refusal(
                    name, action, preview.refusal or "The addon preview is stale; review the changes again."
                )
            try:
                installer.apply_app_names(preview.roots_after, expected_text=preview.settings_text)
            except (OSError, NotImplementedError, StaleAddonPreviewError) as error:
                return InstallResult.refusal(name, action, str(error))
            self.reconcile_from_registry(using, desired=frozenset(preview.roots_after))
            return InstallResult(
                name=name, action=action, already=preview.roots_before == preview.roots_after
            )

    def uninstall(self, name: str, revision: str | None = None) -> InstallResult:
        """Compatibility alias for the canonical disable action."""

        return self.disable(name, revision)

    def change_preview(
        self,
        name: str,
        action: str,
        *,
        installer: AddonInstaller | None = None,
    ) -> AddonChangePreview:
        """Forecast one settings edit against the loaded graph without changing state."""

        if action not in {"install", "disable"}:
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
        available = available_addons(getattr(settings, "ANGEE_ADDON_DIRS", ()))
        manifests = tuple(manifest for manifest, _origin in available.values())
        loaded_configs = {config.name: config for config in composed.addons()}
        roots_before = snapshot.names
        aliases, configs = self._resolve_app_configs(roots_before)
        canonical_roots = tuple(aliases.get(root, root) for root in roots_before)
        refusal: str | None = None
        if action == "install":
            if name not in available:
                refusal = f"{name} is not available to install."
            roots_after = roots_before if refusal or name in canonical_roots else (*roots_before, name)
        else:
            declarations = tuple(root for root in roots_before if aliases.get(root, root) == name)
            refusal = self.disable_block_reason(
                name, loaded=loaded_configs, declared=bool(declarations), aliases=aliases
            )
            roots_after = roots_before if refusal else tuple(root for root in roots_before if root not in declarations)
        after = resolve_manifest_roots(roots_after, manifests, aliases=aliases)
        after_by_name = {manifest.name: manifest for manifest in after}
        loaded_names = set(loaded_configs)
        target_names = set(after_by_name)
        enabled = self._change_impacts(target_names - loaded_names, after_by_name, roots_after, aliases, configs)
        disabled_impacts = []
        for disabled_name in sorted(loaded_names - target_names):
            config = loaded_configs[disabled_name]
            manifest = addon_manifest(config)
            disabled_impacts.append(
                AddonChangeImpact(
                    name=config.name,
                    label=config.label,
                    root=bool(getattr(config, "angee_addon_root", False)),
                    depends_on=tuple(manifest.depends_on) if manifest else (),
                )
            )
        disabled = tuple(disabled_impacts)
        inventory = self._data_inventory((impact.name for impact in disabled), using=self._db)
        warning = (
            "Provisioning may generate and apply schema migrations that remove model data or contributed fields; "
            "the exact database effect cannot be forecast safely before migration planning."
            if disabled else None
        )
        revision = _preview_revision(snapshot.text, manifests, action=action, addon=name, roots_after=roots_after)
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
    def _dependants(manifests: Iterable[AddonManifest], *, aliases: Mapping[str, str]) -> dict[str, list[str]]:
        """Invert the caller's declaration set using canonical dependency names."""

        depended_by: dict[str, list[str]] = {}
        for manifest in sorted(manifests, key=lambda item: item.name):
            for dependency in sorted({aliases.get(name, name) for name in manifest.depends_on}):
                depended_by.setdefault(dependency, []).append(manifest.name)
        return depended_by

    def disable_block_reason(
        self,
        name: str,
        *,
        loaded: Mapping[str, AppConfig],
        declared: bool,
        aliases: Mapping[str, str],
    ) -> str | None:
        """Refuse loaded dependencies or unknown targets, ignoring stale catalogue flags."""

        config = loaded.get(name)
        if config is not None and getattr(config, "angee_forced", False):
            manifests = (
                manifest for candidate in loaded.values() if (manifest := addon_manifest(candidate)) is not None
            )
            required_by = ", ".join(self._dependants(manifests, aliases=aliases).get(name, ())) or "another enabled app"
            return f"{name} is required by {required_by} and cannot be disabled."
        if name not in loaded and not declared and not self.filter(name=name).exists():
            return f"{name} is not known to this project."
        return None

    @staticmethod
    def _resolve_app_configs(declarations: Iterable[str]) -> tuple[dict[str, str], dict[str, AppConfig]]:
        """Resolve authored declarations once, reusing the composed root aliases."""

        aliases = composed.root_app_aliases()
        configs: dict[str, AppConfig] = {}
        for declaration in sorted(set(declarations)):
            config = resolve_app_config(aliases.get(declaration, declaration))
            if config is not None:
                aliases[declaration] = config.name
                configs[config.name] = config
        return aliases, configs

    @staticmethod
    def _change_impacts(
        names: Iterable[str],
        manifests: Mapping[str, AddonManifest],
        roots: Iterable[str],
        aliases: Mapping[str, str],
        configs: Mapping[str, AppConfig],
    ) -> tuple[AddonChangeImpact, ...]:
        declarations = {aliases.get(root, root): root for root in roots}
        impacts = []
        for name in sorted(names):
            config = configs.get(name) or resolve_app_config(name, expected_name=name)
            impacts.append(
                AddonChangeImpact(
                    name=name,
                    label=config.label if config is not None else "",
                    root=name in declarations,
                    depends_on=manifests[name].depends_on,
                )
            )
        return tuple(impacts)

    @staticmethod
    def _data_inventory(names: Iterable[str], *, using: str | None = None) -> tuple[AddonDataInventory, ...]:
        configs = {config.name: config for config in composed.addons()}
        inventories = []
        for name in sorted(names):
            config = configs.get(name)
            if config is None:
                continue
            models_inventory = []
            for model in composed.data_models(config):
                try:
                    count = model._default_manager.using(using).count()
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

    def reconcile_loaded_registry(self, using: str) -> None:
        """Converge after startup using the effective roots recorded by AppGraph."""

        self.reconcile_from_registry(using, desired=composed.root_app_names())

    def reconcile_from_registry(self, using: str, *, desired: frozenset[str] | None) -> None:
        """Converge the table to the composed app graph + available addons.

        A **state** reconcile, never a delete: an addon that leaves the project is
        marked ``REMOVED`` — its row, and so its history, is kept — rather than
        pruned. Scoped to the tier this reconcile owns (installed/local); rows of
        other tiers (the VCS marketplace ``platform_integrate_vcs`` contributes) are
        left untouched. Each present addon's row is a full overwrite so a state flip
        (enabled ↔ disabled) resets every reflected field. Runs under the caller's
        ``system_context`` (see ``signals.py``); routed through ``using`` and wrapped
        in one transaction like the sibling source reconciles, so a mid-loop failure
        never leaves the table half-converged.

        ``desired`` contains authored settings roots. Available roots awaiting
        composition and loaded roots removed from that desired set are pending;
        loaded dependencies never queue a disable merely by being absent from the
        roots. When desired settings are unreadable (``None``), retain each row's
        pending flag. ``depended_by`` inverts available declarations, including
        disabled declarers; it is catalogue metadata.
        """

        available = available_addons(getattr(settings, "ANGEE_ADDON_DIRS", ()))
        loaded = {config.name: config for config in composed.addons()}
        aliases, configs = self._resolve_app_configs(desired or ())
        configs.update(loaded)
        canonical_desired = (
            None
            if desired is None
            else frozenset(aliases.get(declaration, declaration) for declaration in desired)
        )
        manifests = {name: manifest for name, (manifest, _origin) in available.items()}
        for name, config in loaded.items():
            if (manifest := addon_manifest(config)) is not None:
                manifests[name] = manifest
        depended_by = self._dependants(manifests.values(), aliases=aliases)
        counts = composed.resource_counts(using=using)
        rows = self.using(using)
        with transaction.atomic(using=using):
            pending_by_name = dict(rows.values_list("name", "pending")) if canonical_desired is None else {}
            rows.filter(source__in=(Addon.Source.INSTALLED, Addon.Source.LOCAL)).exclude(name__in=manifests).update(
                state=Addon.State.REMOVED,
                **self.model.reset_runtime_facts(),
            )
            for name, manifest in sorted(manifests.items()):
                origin = available[name][1] if name in available else None
                config = configs.get(name)
                if config is None:
                    config = resolve_app_config(name, expected_name=name)
                enabled = name in loaded
                root = enabled and bool(getattr(config, "angee_addon_root", False))
                runtime_facts = self.model.reset_runtime_facts()
                if enabled:
                    data_models = composed.data_models(loaded[name])
                    runtime_facts.update(
                        forced=bool(getattr(config, "angee_forced", False)),
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
                        "namespace": name.split(".")[0],
                        "description": manifest.description,
                        "keywords": list(manifest.keywords),
                        "category": manifest.category or "",
                        "kind": Addon.Kind.CONSUMER if root else Addon.Kind.REQUIRED,
                        "source": Addon.Source.INSTALLED if isinstance(origin, EntryPoint) else Addon.Source.LOCAL,
                        "state": Addon.State.ENABLED if enabled else Addon.State.DISABLED,
                        "pending": pending,
                        "depends_on": list(manifest.depends_on),
                        "depended_by": depended_by.get(name, []),
                    },
                )

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
        desired = AddonInstaller(LocalInstallerBackend()).desired_app_names()
        return None if desired is None else desired != loaded


def _preview_revision(
    text: str,
    manifests: Iterable[AddonManifest],
    *,
    action: str,
    addon: str,
    roots_after: tuple[str, ...],
) -> str:
    """Bind the reviewed action and result to exact settings and manifest facts."""

    payload = [
        {
            "name": manifest.name,
            "depends_on": list(manifest.depends_on),
            "description": manifest.description,
            "category": manifest.category,
        }
        for manifest in sorted(manifests, key=lambda item: item.name)
    ]
    decision = {
        "settings": text,
        "manifests": payload,
        "action": action,
        "addon": addon,
        "roots_after": roots_after,
    }
    return canonical_json_sha256(decision)


class Addon(AngeeModel):
    """The composed-runtime addon registry — local reflection, system-synced.

    Identity is ``name`` (e.g. ``angee.iam``) — the stable key the console
    cross-links on — not a sqid.
    """

    runtime = True

    objects = AddonManager()

    class Kind(models.TextChoices):
        """Whether the project chose this addon (root) or it came in as a dependency."""

        CONSUMER = "consumer", "Consumer"
        REQUIRED = "required", "Required"

    class Source(models.TextChoices):
        """Where the available addon resolved from."""

        INSTALLED = "installed", "Installed"  # an installed bundle's entry point (uv.lock)
        LOCAL = "local", "Local"  # an addon.toml under ANGEE_ADDON_DIRS
        REMOTE = "remote", "Remote"  # known from a VCS source, not materialised (platform_integrate_vcs)

    class State(models.TextChoices):
        """The addon's lifecycle in this project — reconciled, never deleted."""

        ENABLED = "enabled", "Enabled"  # composed into the app graph
        DISABLED = "disabled", "Disabled"  # available/known but not composed
        REMOVED = "removed", "Removed"  # was present, now gone from the env (kept as history)

    name = models.CharField(max_length=200, unique=True)
    label = models.CharField(max_length=100, blank=True, default="")
    namespace = models.CharField(max_length=100, blank=True, default="")
    # Manifest metadata (the addon's ``addon.toml``), reflected for the marketplace
    # board: the freeform ``category`` it groups by and the ``description``/``keywords``
    # the cards show. The contract owns these; the reconcile only mirrors them.
    description = models.TextField(blank=True, default="")
    keywords = models.JSONField(default=list, blank=True)
    category = models.CharField(max_length=100, blank=True, default="", db_index=True)
    kind = StateField(choices_enum=Kind, default=Kind.REQUIRED)
    source = StateField(choices_enum=Source, default=Source.INSTALLED)
    state = StateField(choices_enum=State, default=State.DISABLED, db_index=True)
    # Snapshot of the loaded AppGraph's required-dependency annotation for display.
    # Commands consult that live annotation in AddonManager.change_preview.
    forced = models.BooleanField(default=False, db_index=True)
    # Desired-vs-loaded drift; reconcile_from_registry owns the pending rule.
    pending = models.BooleanField(default=False, db_index=True)
    model_count = models.PositiveIntegerField(default=0)
    field_count = models.PositiveIntegerField(default=0)
    resource_count = models.PositiveIntegerField(default=0)
    # Declared direct dependencies in every state, including the last known
    # manifest when removed.
    depends_on = models.JSONField(default=list, blank=True)
    # Inverse of available declarations, including disabled declarers.
    depended_by = models.JSONField(default=list, blank=True)
    model_labels = models.JSONField(default=list, blank=True)

    class Meta:
        """Django model options."""

        abstract = True
        ordering = ("name",)
        rebac_resource_type = "platform/addon"

    def __str__(self) -> str:
        """Return the addon name for Django displays."""

        return self.name

    @staticmethod
    def reset_runtime_facts() -> dict[str, Any]:
        """Return fresh reset values for a catalogue row outside the loaded graph."""

        return {
            "forced": False,
            "pending": False,
            "model_count": 0,
            "field_count": 0,
            "resource_count": 0,
            "model_labels": [],
            "depended_by": [],
        }


class PlatformExplorer(AngeeModel):
    """Table-less REBAC type anchor for the platform introspection surface."""

    runtime = True

    class Meta:
        abstract = True
        managed = False
        rebac_resource_type = "platform/explorer"
