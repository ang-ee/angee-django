"""Source models for the platform addon.

The platform console reflects the runtime the composer already built. ``Addon`` is
that reflection made persistent: one row per composed/available addon, **converged
from the app registry after migrate** — the same reconcile Django runs for
``django_content_type`` / ``auth_permission`` (see ``signals.py``). It is therefore
authoritatively *derived*, never authored: ``settings.yaml`` (enabled) and
``uv.lock`` (available) remain the source of truth; this table is the queryable
mirror that backs the console.

Scope is deliberately **local**: which addons are *available* (installed bundles +
local ``addon.toml``) and which lifecycle ``state`` each is in — ``enabled``
(composed), ``disabled`` (available but not composed), or ``removed`` (gone from the
env, the row kept as history; the reconcile marks state, it never deletes). The
remote marketplace — addons known from VCS provenance but not materialised — is
**not** here; the ``platform_integrate_vcs`` addon extends ``Addon`` with that tier.

``PlatformExplorer`` stays a table-less REBAC type anchor for the schema explorer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.db import DatabaseError, models, router, transaction
from hatch_angee import AddonManifest
from rebac import system_context

from angee.addons import addon_manifest, available_addons, resolve_manifest_roots
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

        with system_context(reason=f"platform.addon.{action}"):
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
            self.reconcile_from_registry(
                router.db_for_write(self.model), desired=frozenset(preview.roots_after)
            )
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
                action, name, "", False,
                "INSTALLED_APPS is not controlled by a readable project settings.yaml.",
                (), (), (), (), (), None,
            )
        available = available_addons(getattr(settings, "ANGEE_ADDON_DIRS", ()))
        manifests = tuple(ref.manifest for ref in available.values())
        aliases = composed.root_app_aliases()
        loaded_configs = {config.name: config for config in composed.addons()}
        roots_before = snapshot.names
        canonical_roots = tuple(aliases.get(root, root) for root in roots_before)
        refusal: str | None = None
        if action == "install":
            if name not in available:
                refusal = f"{name} is not available to install."
            roots_after = roots_before if refusal or name in canonical_roots else (*roots_before, name)
        else:
            declarations = tuple(root for root in roots_before if aliases.get(root, root) == name)
            row = self.filter(name=name).first()
            if row is not None and row.disable_block_reason:
                refusal = row.disable_block_reason
            elif row is None and name not in loaded_configs and not declarations:
                refusal = f"{name} is not known to this project."
            roots_after = roots_before if refusal else tuple(root for root in roots_before if root not in declarations)
        after = resolve_manifest_roots(roots_after, manifests, aliases=aliases)
        after_by_name = {manifest.name: manifest for manifest in after}
        loaded_names = set(loaded_configs)
        target_names = set(after_by_name)
        enabled = self._change_impacts(target_names - loaded_names, after_by_name, roots_after, aliases)
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
        inventory = self._data_inventory(impact.name for impact in disabled)
        warning = (
            "Provisioning may generate and apply schema migrations that remove model data or contributed fields; "
            "the exact database effect cannot be forecast safely before migration planning."
            if disabled else None
        )
        revision = _preview_revision(snapshot.text, manifests, action=action, addon=name, roots_after=roots_after)
        return AddonChangePreview(
            action, name, revision, refusal is None, refusal, roots_before, roots_after,
            enabled, disabled, inventory, warning, snapshot.text,
        )

    @staticmethod
    def _change_impacts(
        names: Iterable[str],
        manifests: Mapping[str, AddonManifest],
        roots: Iterable[str],
        aliases: Mapping[str, str],
    ) -> tuple[AddonChangeImpact, ...]:
        canonical_roots = {aliases.get(root, root) for root in roots}
        return tuple(
            AddonChangeImpact(
                name=name,
                label=name.rsplit(".", 1)[-1],
                root=name in canonical_roots,
                depends_on=tuple(manifests[name].depends_on) if name in manifests else (),
            )
            for name in sorted(names)
        )

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
                    count = model._default_manager.using(router.db_for_read(model)).count()
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
        """

        aliases = composed.root_app_aliases()
        canonical_desired = (
            None
            if desired is None
            else frozenset(aliases.get(declaration, declaration) for declaration in desired)
        )
        facts = self._registry_facts(desired=canonical_desired)
        rows = self.using(using)
        owned = (Addon.Source.INSTALLED, Addon.Source.LOCAL)
        with transaction.atomic(using=using):
            rows.filter(source__in=owned).exclude(name__in=facts).update(
                state=Addon.State.REMOVED,
                forced=False,
                pending=False,
                model_count=0,
                field_count=0,
                resource_count=0,
                depends_on=[],
                model_labels=[],
                depended_by=[],
            )
            for name, defaults in facts.items():
                if canonical_desired is None:
                    existing = rows.filter(name=name).values_list("pending", flat=True).first()
                    defaults["pending"] = bool(existing) if existing is not None else False
                rows.update_or_create(name=name, defaults=defaults)

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

    @staticmethod
    def _registry_facts(desired: frozenset[str] | None = None) -> dict[str, dict[str, Any]]:
        """Build the complete reflected row for every available-or-enabled addon.

        ``desired`` is the normalized set of ``settings.yaml`` ``INSTALLED_APPS``
        roots, or ``None`` when that source is unreadable. An
        available-but-not-composed addon named in ``desired`` is ``pending`` (just
        installed, awaiting the next boot). A composed consumer root omitted from
        ``desired`` is likewise pending removal; composed dependencies are not.
        """

        rollups = {rollup.name: rollup for rollup in composed.addon_rollups()}  # enabled (composed)
        available = available_addons(getattr(settings, "ANGEE_ADDON_DIRS", ()))
        facts: dict[str, dict[str, Any]] = {}
        for name in sorted(set(rollups) | set(available)):
            rollup = rollups.get(name)
            ref = available.get(name)
            source = ref.source if ref is not None else Addon.Source.LOCAL
            if rollup is not None:
                facts[name] = {
                    "label": rollup.label,
                    "namespace": rollup.namespace,
                    "description": rollup.description,
                    "keywords": rollup.keywords,
                    "category": rollup.category,
                    "kind": rollup.kind,
                    "source": source,
                    "state": Addon.State.ENABLED,
                    "forced": rollup.forced,
                    # Composed but no longer a desired root → a queued *disable* (it
                    # leaves on the next boot). Scoped to roots: a non-root dependency is
                    # never in ``desired`` yet is not being uninstalled. The symmetric
                    # available-branch ``pending`` below is the queued *install*.
                    "pending": (
                        desired is not None
                        and rollup.kind == Addon.Kind.CONSUMER
                        and name not in desired
                    ),
                    "model_count": rollup.model_count,
                    "field_count": rollup.field_count,
                    "resource_count": rollup.resource_count,
                    "depends_on": rollup.depends_on,
                    "model_labels": rollup.model_labels,
                }
            else:  # available but not enabled — a complete row, every count zeroed.
                available_ref = available[name]
                facts[name] = {
                    "label": name.rsplit(".", 1)[-1],
                    "namespace": name.split(".")[0],
                    "description": available_ref.manifest.description,
                    "keywords": list(available_ref.manifest.keywords),
                    "category": available_ref.manifest.category or "",
                    "kind": Addon.Kind.REQUIRED,
                    "source": source,
                    "state": Addon.State.DISABLED,
                    "forced": False,
                    "pending": desired is not None and name in desired,
                    "model_count": 0,
                    "field_count": 0,
                    "resource_count": 0,
                    "depends_on": [],
                    "model_labels": [],
                }
        # Reverse dependencies, inverted across the whole set so a paginated client
        # need not invert depends_on itself.
        depended_by: dict[str, list[str]] = {}
        for name, row in facts.items():
            for dependency in row["depends_on"]:
                depended_by.setdefault(dependency, []).append(name)
        for name, row in facts.items():
            row["depended_by"] = sorted(depended_by.get(name, ()))
        return facts


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
    # Reflected from the composer's dependency closure (``AppGraph`` annotation): an
    # addon another installed addon depends on cannot be disabled.
    # Never re-derived here — the closure owner sets it.
    forced = models.BooleanField(default=False, db_index=True)
    # The desired-vs-actual diff: a root listed in ``settings.yaml`` ``INSTALLED_APPS``
    # but not yet composed into the running app graph (just installed, awaiting the
    # next ``angee dev`` boot) — the board's "to install" / pending-restart badge.
    pending = models.BooleanField(default=False, db_index=True)
    model_count = models.PositiveIntegerField(default=0)
    field_count = models.PositiveIntegerField(default=0)
    resource_count = models.PositiveIntegerField(default=0)
    depends_on = models.JSONField(default=list, blank=True)
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

    @property
    def disable_block_reason(self) -> str:
        """Return why this addon cannot be disabled, or ``""`` when it may be.

        A forced (depended-on) addon — framework core, or anything another installed
        addon needs — cannot be disabled. The policy and
        its wording live on the row that carries ``forced`` (derived from the composer's
        dependency closure); the disable flow only relays this.
        """

        if self.forced:
            dependants = ", ".join(self.depended_by)
            required_by = f" by {dependants}" if dependants else " by another installed addon"
            return f"{self.name} is required{required_by} and cannot be disabled."
        return ""

    @property
    def uninstall_block_reason(self) -> str:
        """Compatibility alias for :attr:`disable_block_reason`."""

        return self.disable_block_reason


class PlatformExplorer(AngeeModel):
    """Table-less REBAC type anchor for the platform introspection surface."""

    runtime = True

    class Meta:
        abstract = True
        managed = False
        rebac_resource_type = "platform/explorer"
