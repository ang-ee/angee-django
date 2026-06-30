# Marketplace install/uninstall — Option B (settings.yaml is the source)

**Status:** plan / contract. Settled design — do not re-litigate (Option B).
**Terminology:** INSTALL / UNINSTALL / INSTALLED only (Django `INSTALLED_APPS`-coherent).
Never enable/disable/desired in any new name. (The existing `Addon.State` enum keeps
its current `enabled/disabled/removed` values — that is a separate, already-shipped
column about *composed-vs-not*, not the install verb; see "Naming" below.)

## 0. The settled design in one paragraph

`settings.yaml` `INSTALLED_APPS` is the **source** of the addon set; the Django app
reads it at boot exactly as today (no DB-driven settings-load, no SQL at boot).
`platform.Addon` stays the read-only **reflection** (system-synced at `post_migrate`).
"Install an addon" = add its root to `settings.yaml` `INSTALLED_APPS`, then
rebuild+restart. The **`AddonInstaller`** service owns the YAML logic (read →
ruamel comment-preserving edit of `INSTALLED_APPS` → write → request rebuild); a
settings-keyed **backend** is the pure transport (file read / file write / rebuild),
selected like an `ImplClassField` registry with a `"local"` default. The `local`
backend (dev stub) edits the local `settings.yaml` and treats rebuild as a pending
marker (the addon composes on the next `angee dev` boot); the `operator` backend is a
skeleton that raises `NotImplementedError` pointing at
`.agents/proposals/operator-file-tools.md`. `forced` (cannot uninstall) is derived
from the composer's dependency closure; `pending` (in `settings.yaml` but not yet
composed) is the desired-vs-actual diff. GraphQL `install`/`uninstall` (platform) and
`addSource`/`scan` (platform_integrate_vcs) drive it, REBAC-gated like the other
platform/console mutations, and the Odoo-style **Apps board** composes the shared
`ListView` board primitive over the `platform.Addon` resource.

## 1. Architecture gate (AGENTS.md)

**Owner map (fact → owner):**
- *Which addons the project installs* → `settings.yaml` `INSTALLED_APPS` (unchanged
  boot source). The **edit** of that file → the new `AddonInstaller` (one writer).
- *How a settings.yaml edit reaches disk / triggers a rebuild* → the
  `AddonInstallerBackend` transport (`local` / `operator`), selected by a settings
  registry key — the `ImplClassField` resolution contract (trusted settings +
  `import_string` + `base_class` check + a `manage.py check`).
- *Comment-preserving YAML round-trip* → `ruamel.yaml` (new locked dep; pyyaml can't
  round-trip comments).
- *Forced (not-uninstallable)* → the composer's dependency closure
  (`AppGraph.resolve`), surfaced as a per-`AppConfig` annotation, read by the rollup —
  exactly like the existing `angee_addon_root` / `angee_depends_on` annotations. Never
  re-derived inside the reflection.
- *Pending (desired-but-not-composed)* → the diff between the installer's
  `settings.yaml` roots (desired) and the composed app registry (actual).
- *Reflected addon row* → `platform.Addon` (`AddonManager.reconcile_from_registry`),
  the existing system-synced owner; new columns join the same reconcile.
- *Addon metadata (description/keywords/category)* → `AddonContract` (the manifest),
  surfaced through the existing `available_addons` / `addon_rollups` derivations.
- *Marketplace REMOTE rows + addon `Source`* → `platform_integrate_vcs` (existing
  `AddonCatalog` + integrate `VcsBridge`/`Repository`/`Source`).
- *Install/uninstall/addSource/scan transport at the edge* → thin Strawberry
  resolvers that gate the actor and dispatch to the owner above.
- *Apps board UI* → the shared `ListView`/`BoardView` primitive (compose, never
  hand-roll); the VCS source controls → a toolbar **slot** the VCS-tier web fills.

**Sibling inventory (same shape ≥2 places):**
- ImplClassField registry + settings default + autoconfig: `VcsBridge.backend_class`
  (`ANGEE_VCS_BACKEND_CLASSES`), `storage.Backend.backend_class`
  (`ANGEE_STORAGE_BACKEND_CLASSES`), `Integration.impl_class`
  (`ANGEE_INTEGRATION_IMPLS`). The installer follows this registry contract (row-less
  variant — see §4).
- Composer-closure annotation read by the rollup: `angee_addon_root` /
  `angee_depends_on` (`AppGraph.resolve` → `composed.addon_rollups`). `forced` is the
  third annotation of the same kind.
- Console action mutation returning `ActionResult` + `action_target` + admin gate:
  integrate `VCSActionMutation.refresh_source` / `sync_vcs_bridge`, iam/integrate
  `_ADMIN_PERMISSION_CLASSES`. install/uninstall/addSource/scan copy this shape.
- VCS source reconcile from a repo: integrate `TemplateManager.sync_from_source` and
  `platform_integrate_vcs.AddonCatalogManager.sync_from_source` (already wired to
  `Source.refresh()`); `scan` reuses `Source.refresh()`, never a new walk.
- Board toolbar control contributed by an addon: `RepositoriesPage`
  `toolbarActions={<AddRepositoryControl/>}`; login-page slots (`useSlot` /
  `SlotOutlet` / `SlotContribution`). Add Source/Scan compose both.

**Dependency check:** new locked dep = `ruamel.yaml` (stack row + `pyproject.toml`
together, §2). Everything else composes locked libs already present.

**Thin-caller check:** the four resolvers parse input, resolve actor (admin
`permission_classes`), and dispatch to `AddonInstaller` / `Source` / `VcsBridge`.
No business rules in resolvers.

**Deletion check:** this is net-additive (a new capability), but it deletes the
*concept* of a separate TS-side YAML editor that the operator proposal floated
(superseded — see §9) and unifies "who edits settings.yaml" onto one Python owner.
The `scan` verb deletes the need for a marketplace-only re-implementation of source
sync (it forwards to `Source.refresh()`). No line-count justification beyond a new
feature; each new owner is the smallest at its level.

**Naming check:** `install` / `uninstall` everywhere (verbs, mutations, board
actions, i18n, installer methods). `forced` / `pending` for the two new booleans.
The **Apps board** is the new install/marketplace surface (`platform.apps`); the
existing **Addons** list (`platform.addons`) stays the technical schema-explorer
inventory — two intents over one `platform.Addon` resource, the same split Odoo draws
between *Apps* and a technical module list. The pre-existing `Addon.State`
(`enabled/disabled/removed`) is left untouched: it names *composed-or-not*, a
different fact from the install verb, and renaming it is out of scope and would churn
a shipped migration/enum. New code never introduces enable/disable synonyms.

---

## 2. Stage A — `ruamel.yaml` dependency

**`pyproject.toml`** (`dependencies` list): add `"ruamel.yaml>=0.18"`.
Run `uv lock` (updates `uv.lock`).

**`docs/stack.md`** Backend table — add a row (keep alphabetical-ish near the YAML
owners):

```
| ruamel.yaml | Comment/format-preserving round-trip YAML editing | The `AddonInstaller`'s `settings.yaml` `INSTALLED_APPS` install/uninstall edit — the one writer that must preserve operator comments and layout (pyyaml round-trips lose them) |
```

Leave the existing `pyyaml` / `django-yamlconf` rows: pyyaml still parses resource
YAML and django-yamlconf still consumes settings overlays at boot. ruamel is used
**only** by the installer's comment-preserving edit, not at boot.

**Verify:** `uv lock` clean; `uv run python -c "import ruamel.yaml"`.

---

## 3. Stage B — `forced` from the composer closure

The closure owner annotates each resolved `AppConfig`; the rollup reads it; the
reflection stores it. No new walk, no re-derivation in the reflection.

### `angee/compose/appgraph.py` — `AppGraph.resolve`
After computing `ordered`, collect the set of names **depended upon by any resolved
config** and annotate each config:

```python
depended_upon: set[str] = set()
for config in ordered:
    for dep in self.app_dependencies(config):
        depended_upon.add(aliases.get(dep, dep))
for config in ordered:
    config.angee_addon_root = config.name in root_name_set
    config.angee_depends_on = self.app_dependencies(config)
    config.angee_forced = config.name in depended_upon
```

`forced = "another installed addon depends on me"`. Transitivity falls out (A→B→C
puts both B and C in `depended_upon`). This is precisely "framework core + anything
another enabled addon depends on": framework core (`angee.compose/base/graphql`) is
always depended upon; a leaf consumer/product root (e.g. `example.notes`) and the
`platform_integrate_vcs` tier are **not** depended upon → uninstallable. Extend the
class docstring's annotation list with `angee_forced`.

### `addons/angee/platform/composed.py` — `AddonRollup`
Add `forced: bool` (and, for the board cards, `description: str`,
`keywords: list[str]`, `category: str`). In `addon_rollups()`:

```python
from angee.addons import addon_contract  # already an allowed import level
...
contract = addon_contract(config)
AddonRollup(
    ...,
    forced=bool(getattr(config, "angee_forced", False)),
    description=(contract.description if contract else ""),
    keywords=sorted(contract.keywords) if contract else [],
    category=(contract.category or "") if contract else "",
)
```

`composed.py` reads only `AppConfig` annotations + the manifest contract — it does
**not** import `angee.compose` (layering preserved).

### Tests
`tests/test_compose.py`: extend the annotation test to assert `angee.resources`
(pulled into iam's closure) has `angee_forced is True` and an undepended root has
`angee_forced is False`.

**Verify:** `uv run python -m pytest tests/test_compose.py`.

---

## 4. Stage C — `platform.Addon` reflection: new columns + reconcile + migration

### `addons/angee/platform/models.py`

Add five reflected columns to `Addon` (all derived, read-only over GraphQL):

```python
description = models.TextField(blank=True, default="")
keywords = models.JSONField(default=list, blank=True)
category = models.CharField(max_length=100, blank=True, default="")
forced = models.BooleanField(default=False, db_index=True)   # cannot be uninstalled
pending = models.BooleanField(default=False, db_index=True)  # in settings.yaml, not yet composed
```

`AddonManager.reconcile_from_registry` — extend the two `_registry_facts` branches and
the REMOVED-reset `update(...)` to cover the new columns, and compute `pending` from
the installer's desired roots:

- **Desired roots:** read once, best-effort, from the installer (one settings.yaml
  owner):
  ```python
  from angee.platform.installer import addon_installer
  desired = set(addon_installer().installed_app_names())  # () if unreadable
  ```
  Best-effort: `installed_app_names()` returns `()` when the file is absent/unreadable
  (bare test settings, or the `operator` backend not active) so the reconcile never
  raises and `pending` simply stays `False`.
- **ENABLED branch** (rollup present): `forced=rollup.forced`,
  `description/keywords/category=rollup.*`, `pending=False` (it *is* composed).
- **Available-but-not-enabled branch:** `forced=False`, metadata from the
  `AvailableAddon` (see below), `pending=(name in desired)` → the "to install" badge.
- **REMOVED-reset `update(...)`:** also reset `forced=False`, `pending=False`,
  `description=""`, `keywords=[]`, `category=""`.

`pending` is correct because the **installer is the only writer of
`settings.yaml`'s `INSTALLED_APPS`**, and every install/uninstall mutation re-runs the
reconcile (§6), so the stored value is fresh exactly when it matters (after migrate
and after each install/uninstall action); between those, the file does not change.

### `angee/addons.py` — `AvailableAddon` metadata (framework-core, single toml read)
`available_addons` already opens each local `addon.toml` to read `name`; capture the
manifest metadata there so the reflection has it for not-yet-enabled local addons:

```python
@dataclass(frozen=True, slots=True)
class AvailableAddon:
    name: str
    source: str
    anchor: str
    description: str = ""
    keywords: tuple[str, ...] = ()
    category: str = ""
```

Populate `description`/`keywords`/`category` from the parsed `[addon]` block in the
local branch. Installed entry-point bundles that are not enabled keep blanks
(documented edge — an enabled bundle gets full metadata via the rollup). These are
additive optional fields; existing call sites are unaffected.

### `addons/angee/platform/schema.py` — `AddonNode` + Hasura resource
Add the new fields to `AddonNode` (booleans/str/list project natively; keep
`kind/source/state` as `str` per the existing enum-collision note):

```python
description: auto
keywords: list[str]
category: auto
forced: auto
pending: auto
```

Update `_ADDON_RESOURCE = hasura_model_resource(AddonNode, ...)`:
- `filterable`: add `"category"`, `"forced"`, `"pending"` (source/state already there).
- `sortable`: add `"category"`.
- `groupable`: add `"category"` (board groups by it; keep namespace/kind/source/state).
Leave `insert=False, update=False, delete=False` (still system-synced/read-only).

### Migration
Source models are abstract; the composer emits the concrete `platform` app. After
`angee build`, run `makemigrations platform` (name the app — see the
makemigrations pitfall). The migration lands under `runtime/platform/migrations/`
(generated output) — created by the command, not hand-written. The `post_migrate`
reconcile (`signals.py`, unchanged) repopulates every row including the new columns.

### Tests
`tests/test_addons.py`: extend `_registry_facts` assertions — an enabled
framework/base addon (e.g. `angee.resources`) has `forced True`; a not-enabled local
fixture addon has `forced False` and `pending` reflecting a stub desired set; metadata
flows from the contract.

**Verify (Stage B+C together):**
```sh
uv run examples/notes-angee/manage.py angee build
uv run examples/notes-angee/manage.py makemigrations platform
uv run examples/notes-angee/manage.py migrate
uv run examples/notes-angee/manage.py rebac sync
uv run python -m pytest tests/test_addons.py tests/test_compose.py
```

---

## 5. Stage D — the `AddonInstaller` seam

### New: `addons/angee/platform/installer.py`

**Transport base + two backends** (the `ImplClassField` registry shape, row-less —
there is no per-row choice, it is a per-deployment settings choice, so the selection
is a settings key resolved against a registry, not a model column):

```python
class AddonInstallerBackend:
    """Pure transport for the settings.yaml that lists INSTALLED_APPS + the rebuild.
    The AddonInstaller owns all YAML logic; a backend only moves bytes and asks for a
    rebuild. Subclasses: `local` (key), `operator` (key)."""
    key: ClassVar[str] = ""
    def read_settings_text(self) -> str: raise NotImplementedError
    def write_settings_text(self, text: str) -> None: raise NotImplementedError
    def request_rebuild(self) -> str: raise NotImplementedError   # returns a status marker

class LocalInstallerBackend(AddonInstallerBackend):
    key = "local"
    # path = Path(settings.BASE_DIR) / "settings.yaml"; read_text / write_atomic.
    # request_rebuild() -> "pending" (no-op; recomposes on next `angee dev` boot).

class OperatorInstallerBackend(AddonInstallerBackend):
    key = "operator"
    # every method raises NotImplementedError naming
    # `.agents/proposals/operator-file-tools.md`:
    #   read_settings_text -> operator GET /files?source=app&path=settings.yaml
    #   write_settings_text -> operator PUT /files (+ etag)
    #   request_rebuild    -> operator POST /stack/build (+ restart)
```

Write via `angee.fs.write_atomic` (the same atomic writer the composer uses).
`LocalInstallerBackend` reads/writes `settings.yaml` beside `manage.py`
(`settings.BASE_DIR`); missing file → `read_settings_text` raises `FileNotFoundError`
which `installed_app_names()` swallows to `()`.

**The installer (owns the ruamel YAML edit):**

```python
class AddonInstaller:
    def __init__(self, backend: AddonInstallerBackend): self.backend = backend

    def installed_app_names(self) -> tuple[str, ...]:
        """Desired INSTALLED_APPS roots from settings.yaml; () when unreadable."""
        # try read_settings_text(); ruamel round-trip load; return tuple(seq) or ()
        # swallow FileNotFoundError / NotImplementedError -> ()

    def install(self, name: str) -> InstallResult:
        # read -> ruamel round-trip -> if name not in INSTALLED_APPS: append (preserve
        # author order + comments) -> write -> request_rebuild() -> InstallResult
    def uninstall(self, name: str) -> InstallResult:
        # read -> ruamel -> remove name if present -> write -> request_rebuild()
```

`InstallResult` = a small dataclass `{name, action, already, rebuild_status}` the
resolver maps to `ActionResult`. The ruamel edit uses `YAML(typ="rt")`
(round-trip) and edits the `INSTALLED_APPS` `CommentedSeq` in place (append on
install; `seq.remove(name)` on uninstall) so operator comments and key order survive.
Append (not sort): preserve the author's ordering; the composer sorts the closure
deterministically at boot regardless.

**Resolution (faithful to the `ImplClassField` contract, row-less):**

```python
def addon_installer() -> AddonInstaller:
    key = getattr(settings, "ANGEE_ADDON_INSTALLER_BACKEND", "local")
    registry = getattr(settings, "ANGEE_ADDON_INSTALLER_BACKEND_CLASSES", {})
    backend_cls = import_string(registry[key])           # trusted, composed settings
    if not (isinstance(backend_cls, type) and issubclass(backend_cls, AddonInstallerBackend)):
        raise ImproperlyConfigured(...)
    return AddonInstaller(backend_cls())
```

This mirrors `ImplClassField.resolve_class` (trusted settings path, never row text,
`base_class` check) without the model-column/GraphQL-enum machinery a singleton
service does not need.

**System check (mirrors `ImplClassField.check`):** a `@register()` check that imports
every `ANGEE_ADDON_INSTALLER_BACKEND_CLASSES` path and verifies it subclasses
`AddonInstallerBackend`, and that `ANGEE_ADDON_INSTALLER_BACKEND` names a known key.
Register it from `PlatformConfig.ready()` (import the module so the check binds).

### New: `addons/angee/platform/autoconfig.py`
```python
SETTINGS = {
    "ANGEE_ADDON_INSTALLER_BACKEND": "local",
    "ANGEE_ADDON_INSTALLER_BACKEND_CLASSES": {
        "local": "angee.platform.installer.LocalInstallerBackend",
        "operator": "angee.platform.installer.OperatorInstallerBackend",
    },
}
```
The composer reads each addon's optional `autoconfig.py`. The `operator` key is
registered but **not** the default; production flips
`ANGEE_ADDON_INSTALLER_BACKEND="operator"` via stack settings.

### `addons/angee/platform/apps.py`
In `ready()`, also import `installer` so its registered system check binds (one extra
import line beside the existing `signals.connect()`).

### `tests/settings.py`
Add the two installer settings (bare test settings skip the composer/autoconfig, so
every settings module that installs platform must carry the registry — the
documented `ImplClassField`-registry rule applies to this row-less registry too):
```python
ANGEE_ADDON_INSTALLER_BACKEND = "local"
ANGEE_ADDON_INSTALLER_BACKEND_CLASSES = {
    "local": "angee.platform.installer.LocalInstallerBackend",
    "operator": "angee.platform.installer.OperatorInstallerBackend",
}
```

### Tests
New `tests/test_addon_installer.py`:
- ruamel round-trip preserves a comment + key order across an install then uninstall
  (drive `LocalInstallerBackend` against a temp `settings.yaml` via `settings.BASE_DIR`
  override / a `tmp_path` fixture).
- `install` is idempotent (`already=True` when present); `uninstall` of an absent name
  is a no-op.
- `OperatorInstallerBackend.read_settings_text()` raises `NotImplementedError`
  mentioning the proposal path.
- `addon_installer()` resolves `local` and rejects an unknown/non-subclass key.

**Verify:**
```sh
uv run python -m pytest tests/test_addon_installer.py
uv run examples/notes-angee/manage.py check
```

---

## 6. Stage E — GraphQL mutations

### `addons/angee/platform/schema.py` — install / uninstall (console)
Gate with the platform-admin classes (platform already depends on iam):

```python
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.graphql.actions import ActionResult
from angee.platform.installer import addon_installer

@strawberry.type
class AddonInstallMutation:
    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def install(self, addon: str) -> ActionResult:
        with system_context(reason="platform.graphql.install"):
            result = addon_installer().install(addon)
            _Addon.objects.reconcile_from_registry(using=router.db_for_write(_Addon))
        return ActionResult(ok=True, message=_install_message(result))

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def uninstall(self, addon: str) -> ActionResult:
        with system_context(reason="platform.graphql.uninstall"):
            row = _Addon.objects.filter(name=addon).first()
            if row is not None and row.forced:
                return ActionResult(ok=False, message=f"{addon} is required and cannot be uninstalled.")
            result = addon_installer().uninstall(addon)
            _Addon.objects.reconcile_from_registry(using=router.db_for_write(_Addon))
        return ActionResult(ok=True, message=_uninstall_message(result))
```

- **Refuse forced uninstall** by reading the reflection's `forced` (derived from the
  closure, §3) — the policy lives on the row, the resolver only reports it.
- Re-run the reconcile in-process so the board reflects the new `pending`/`state`
  immediately (dev: addon becomes `ENABLED` only after the next `angee dev` boot, but
  `pending` flips now so the board shows "to install" → "pending").
- Register both in the `console` `mutation` bucket; add `ActionResult` to the
  `console` `types` bucket (platform does not yet export it).

### `addons/angee/platform_integrate_vcs/schema.py` — addSource / scan (console)
This addon already contributes `type_extensions`; add `query`/`mutation` buckets.
Resolve integrate models via the app registry (it depends on integrate):

```python
VcsBridge = apps.get_model("integrate", "VcsBridge")
Repository = apps.get_model("integrate", "Repository")
Source = apps.get_model("integrate", "Source")

@strawberry.input
class AddonSourceInput:
    vcs_bridge_id: PublicID      # an existing bridge (local checkout in dev, or a host bridge)
    name: str = ""               # repo owner/name (optional for the single local repo)
    ref: str = ""
    path: str = ""

@strawberry.type
class MarketplaceSourceMutation:
    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def add_source(self, data: AddonSourceInput) -> ActionResult:
        with action_target(VcsBridge, data.vcs_bridge_id, reason="...add_source") as vcs:
            repository = vcs.import_repository(data.name)          # existing integrate owner
            Source.objects.create(repository=repository, kind="addon", ref=data.ref, path=data.path)
        return ActionResult(ok=True, message="Addon source added.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def scan(self, source_id: PublicID) -> ActionResult:
        with action_target(Source, source_id, reason="...scan") as source:
            if source.kind != "addon":
                return ActionResult(ok=False, message="Not an addon source.")
            count = source.refresh()                              # existing dispatch -> AddonCatalog.sync_from_source
        return ActionResult(ok=True, message=f"Discovered {count} addon(s).")
```

- `add_source` **composes** integrate's `VcsBridge.import_repository` (inventory) +
  `Source.create(kind="addon")`; it does not reinvent bridge/repo creation (the bridge
  is created via integrate's existing `create_vcs_bridge`; the form offers a picker
  with a link to create one). This is the "create a VcsBridge + Source(kind='addon')"
  flow expressed through the existing owners.
- `scan` forwards to `Source.refresh()` — the same owner integrate's `refresh_source`
  uses and the `marketplace sync` command drives — wrapped as a marketplace-named verb
  that validates `kind=="addon"` and reports the discovered count.
- Register `MarketplaceSourceMutation` in the `console` `mutation` bucket and
  `AddonSourceInput` in `types`. Import `ActionResult`, `action_target`, `PublicID`,
  `_ADMIN_PERMISSION_CLASSES` from the same owners integrate uses.
- `platform_integrate_vcs` has no `apps.py`/`autoconfig.py` and needs none for this.

### SDL + tests
After `angee build`, regenerate SDL (`manage.py schema` + `--check`). Add resolver
tests under `tests/test_platform_integrate_vcs.py` (or a new
`tests/test_marketplace_graphql.py`) and platform install/uninstall tests
(`tests/test_platform_install.py`): a forced addon refuses uninstall; install of an
available addon appends to a temp settings.yaml and flips `pending`; `scan` over a
local addon `Source` discovers the example's `addon.toml`s into REMOTE rows. Gate-deny
tests: a non-admin actor gets denied (mirror integrate's mutation tests; remember the
`force_login(..., backend="django.contrib.auth.backends.ModelBackend")` pitfall).

**Verify:**
```sh
uv run examples/notes-angee/manage.py angee build
uv run examples/notes-angee/manage.py schema --check
uv run python -m pytest tests/test_platform_install.py tests/test_marketplace_graphql.py
```

---

## 7. Stage F — Frontend: the Apps board + VCS source controls

### `platform` web — the Apps board (composes `ListView` board mode)
New route/page; **compose** the shared primitive, never hand-roll a grid.

- **`addons/angee/platform/web/src/views/AppsBoard.tsx`** — `ListView<AddonRow>` over
  `resource="platform.Addon"` with:
  - `defaultView="board"`, `defaultGroup={{ field: "category" }}`,
    `groupOptions` for `category` / `source` / `state` (the resource is a client row
    model — `platform.Addon` is fetched whole and grouped client-side, as the existing
    `AddonsPage` already does).
  - `columns`: title (label + id), `description`, `keywords` (chips), `source` (badge),
    `state` (badge), `forced` (lock indicator), `pending` (badge). The first column is
    the card title; the next render as card detail rows (`BoardView` shows the first 4
    non-group columns).
  - `cardActions={(row, ctx) => <AddonCardActions row={row} refresh={ctx.refresh} />}`:
    **Install** when `state !== "enabled"` (available/remote/pending); **Uninstall**
    when `state === "enabled" && !row.forced`; a disabled/locked control with a tooltip
    when `row.forced`. Each calls the authored mutation then `ctx.refresh()`.
  - `toolbarActions={<SlotOutlet entries={useSlot(PLATFORM_APPS_TOOLBAR_SLOT)} />}` so
    the VCS tier can inject Add Source / Scan without platform depending on integrate.
- **`addons/angee/platform/web/src/documents.console.ts`** — authored install/uninstall
  documents against `@angee/gql/console` (the `documents.console.ts` filename routes
  to the console schema in the codegen pass):
  ```ts
  export const PlatformInstallAddon = graphql(`mutation PlatformInstallAddon($addon: String!){ install(addon:$addon){ ok message } }`);
  export const PlatformUninstallAddon = graphql(`mutation PlatformUninstallAddon($addon: String!){ uninstall(addon:$addon){ ok message } }`);
  ```
  Invoke via `useAuthoredMutation(..., { invalidateModels: ["platform.Addon"] })`
  (the same hook `AddRepositoryControl` uses).
- **`addons/angee/platform/web/src/index.ts`** — add the route
  `{ name: "platform.apps", path: "/platform/apps", layout: "console",
  resource: "platform.Addon", component: AppsBoard }`, a menu item ("Apps", icon
  `"store"`/`"grid"`) under the Platform group, and `export const
  PLATFORM_APPS_TOOLBAR_SLOT = "platform.apps.toolbar";` for the VCS tier to fill.
- **i18n** (`addons/angee/platform/web/src/i18n.ts`): `platform.apps.*` labels
  (install/uninstall/required/pending/category lane fallback "Other").

### `platform_integrate_vcs` web — Add Source / Scan (new web package)
The REMOTE/source tier owns these controls; they reference integrate's `VcsBridge`
relation + the addon `Source`, so they live in the VCS-tier web (which depends on
integrate's web), contributed into platform's toolbar slot.

- New package `addons/angee/platform_integrate_vcs/web/` modeled on a minimal existing
  addon web package (`package.json` name `@angee/platform-integrate-vcs` or per
  convention, `tsconfig.json`, `vitest.config.ts`, `src/`). Add it to
  `pnpm-workspace.yaml` and run `pnpm install`. The `addon.toml` web seam is inferred
  from `web/package.json` (no manifest change needed). Run `pnpm install` and delete
  stale runtime artifacts per the "after adding/moving an addon" pitfall.
- **`src/AddAddonSourceControl.tsx`** — modeled on integrate's `AddRepositoryControl`:
  a toolbar button opening a dialog with the `VcsBridge` `RelationField` picker + repo
  name / ref / path inputs → `add_source` (authored mutation), `invalidateModels:
  ["platform.Addon", "integrate.Source"]`.
- **`src/ScanControl.tsx`** — a control to pick an addon `Source` and run `scan`
  (authored mutation), invalidating `platform.Addon`; surfaces the discovered count.
- **`src/documents.console.ts`** — authored `MarketplaceAddSource` / `MarketplaceScan`
  documents against `@angee/gql/console`.
- **`src/index.ts(x)`** — `defineBaseAddon({ id: "platform_integrate_vcs", slots: [
  { slot: PLATFORM_APPS_TOOLBAR_SLOT, id: "add-source", sequence: 1, content:
  <AddAddonSourceControl/> }, { slot: ..., id: "scan", sequence: 2, content:
  <ScanControl/> } ] })`, importing `PLATFORM_APPS_TOOLBAR_SLOT` from `@angee/platform`.
- Register the new web addon in the app composition wherever addon web entries are
  enumerated (mirror how `platform`'s web entry is wired into `@angee/app`).

### Codegen
`platform.Addon` is already an emitted resource; the new fields ride its existing
metadata. After `angee build` + `manage.py schema`, run the established codegen pass
(`angee-web-codegen`, the one `@angee/app` CLI that reads
`runtime/web/manifest.json`) so the authored install/uninstall/addSource/scan
documents type-check against the regenerated console SDL. `documents.console.ts`
filenames route to the console schema automatically.

### Frontend tests
- `AppsBoard` renders board lanes grouped by category; a `forced` row's Uninstall is
  disabled; a `pending` row shows the badge; install/uninstall call the right mutation
  (mirror existing `*.test.tsx` patterns, happy-dom + testing-library).
- `AddAddonSourceControl` / `ScanControl` invoke their documents (mirror
  `AddRepositoryControl.test.tsx`).

**Verify:**
```sh
pnpm install
# regenerate types after the SDL exists:
pnpm --filter @angee/app codegen        # or the repo's codegen script
pnpm --filter @angee/platform test
pnpm --filter @angee/platform-integrate-vcs test
pnpm -w typecheck                       # or per-package tsc --noEmit
```

---

## 8. Per-stage verification (roll-up) + final gate

Backend (module form — bare `uv run pytest`/`mypy` fail on this venv):
```sh
uv run python -m ruff check . --no-cache
uv run python -m mypy angee addons
uv run python -m vulture
uv run python -m pytest
uv run examples/notes-angee/manage.py angee build
uv run examples/notes-angee/manage.py makemigrations platform
uv run examples/notes-angee/manage.py migrate
uv run examples/notes-angee/manage.py rebac sync
uv run examples/notes-angee/manage.py schema --check
uv run examples/notes-angee/manage.py check
```
Frontend: `pnpm install`, codegen, `pnpm test`, typecheck (per package above).
End-to-end smoke (optional, via `angee dev`): board lists addons grouped by category;
Install an available addon → `pending` badge; restart `angee dev` → it composes
(`ENABLED`); Uninstall a forced addon is refused; Add Source + Scan over the local
checkout discovers `addon.toml`s into REMOTE rows.

## 9. Decisions, edges, and the operator handoff

- **Proposal reconciliation:** `.agents/proposals/operator-file-tools.md` floated the
  *board* doing the YAML edit in TS. The settled design moves YAML ownership to the
  Python `AddonInstaller` (one writer, comment-preserving via ruamel); the operator
  backend is therefore **pure byte transport** (FileRead/FileWrite) + `/stack/build`,
  exactly the generic file API the proposal scopes. Update that proposal's "Open
  question 1 (who owns YAML)" to "decided: the AddonInstaller owns YAML; the operator
  file API stays raw bytes." The `OperatorInstallerBackend` skeleton's
  `NotImplementedError` messages name those three operator endpoints.
- **No DB-at-boot:** `settings.yaml` stays the boot source. Do **not** reintroduce
  `angee/compose/installed.py` (the reverted DB-driven settings-load). The installer
  edits the file; the next compose reads it — the only flow.
- **`forced` for an undepended host addon:** the Apps board itself lives in `platform`;
  if only `platform` is installed and nothing depends on it, it is technically
  uninstallable (forced=False). Acceptable — uninstalling the board's host addon simply
  removes the board on the next boot, the same as any leaf root. The closure definition
  stays clean rather than special-casing a framework-essentials list.
- **`pending` freshness:** correct because the installer is the sole `settings.yaml`
  `INSTALLED_APPS` writer and every install/uninstall re-runs the reconcile; between
  those the file is unchanged. Bare/operator environments where the file is unreadable
  degrade to `pending=False`, never an error.
- **Migrations:** `runtime/platform/migrations/*` is generated output — produced by
  `makemigrations platform`, never hand-edited; preserved by clean/reset.
