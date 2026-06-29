# Addon System Architecture — distribution, composition, registry, control plane

Status: **core implemented; this stays the SSOT for the rest.** This captures the
model for how Angee addons are *packaged, distributed, discovered, composed,
managed, and run*. Distribution, discovery, the available-set enumerator, and the
local reflection are built (see **Implementation status**). Two decisions evolved
past the first draft and are reflected inline: the restart-surviving "what am I" is
a **DB-model reflection** (§5 — not a `runtime/` artifact), and the **marketplace is
`platform_integrate_vcs`** (§7 — not the operator console). Where the doc still
changes existing mechanisms (AppConfig discovery, `INSTALLED_APPS`, the
`templates/` layout) those remain migrations, not facts.

## Implementation status

- **Done.** `hatch-angee` is published to PyPI as the build **backend**
  (`build-backend = "hatch_angee.build"`, in its own repo); a consumer repo declares
  nothing but the backend. `addon.toml` markers generate the `angee.addons` entry
  points; `available_addons()` enumerates them over `uv.lock` (+ an
  `ANGEE_ADDON_DIRS` scan). `platform.Addon` (`AngeeModel runtime=True`) is synced on
  `post_migrate` (the content-type pattern, §5) and backs the `platform.Addon`
  resource. `django-angee` dogfoods the backend from PyPI.
- **Next.** `addon.toml` carries the full contract — `depends_on` plus
  `permissions`/`schema`/`resources` (explicitly ordered) extracted from `apps.py`,
  which demotes to the `python` seam (§2/§4); `platform_integrate_vcs` marketplace
  (§7); `enabled ⊆ available` validation in the composer (§4).

## The one-line shape

```
bundle (repo@sha, ships 1..N addons, what uv installs)
  → addon (addon.toml = the contract; the atom you enable)
    → contributions (typed seams: python · web · templates · skills · services · schema · permissions · resources)

available = uv.lock (installed bundles)   ·   enabled = settings.yaml (INSTALLED_APPS, Django-managed)
operator owns the lifecycle (grant write + uv install + restart-to-apply); runtime owns the declaration + semantics
```

## Core principles (each grounded in prior art)

1. **Provenance is identity; the artifact is a cache.** A unit's identity is
   `repo@sha (+ subpath)`. A wheel, a rendered template, a loaded skill are all
   *materializations* of that source at that commit. "Build from source" and
   "install from a registry" stop being in tension: the registry is a
   **content-addressed build-cache over VCS provenance** (cf. SLSA / sigstore /
   PEP 740 attestations). `uv add git+…@sha` and an index install both *produce a
   wheel* — the wheel is a build output, not a distribution choice.

2. **One distribution channel, not two.** Python and JS ship together in **one
   wheel** (the JS rides inside via a build hook), consumed via uv. We do **not**
   publish a parallel npm registry. The decisive reason is Angee-specific: addon
   JS is **composed against the project's generated schema** — `web/src` holds
   `graphql(…)` documents typed against `@angee/gql/<schema>`, and codegen binds
   them to the *composed* SDL at project build. So addon JS is **composition-
   dependent source**, not an independently-buildable artifact; a pre-built npm
   `dist` would bake in a schema version and drift. JS must be resolved *into the
   build context* → the wheel. (Closest ancestor: **Odoo** — source modules, the
   framework composes/bundles, no registry split. Not **JupyterLab-prebuilt**,
   whose extensions *are* independently buildable.) One channel ⇒ the marketplace
   is a **single** registry, not a PyPI+npm pair to keep in sync.

3. **The addon is the unit; everything else is a contribution.** Not a generic
   "unit" above the addon, and not sibling kinds. The addon already contributes
   models/schema/web/permissions/resources/mcp; templates, skills, and services
   are **the same pattern, more seams**. A template-only or frontend-only addon is
   just an addon that fills only those seams. (cf. **Odoo `__manifest__.py`** and
   **VS Code `contributes`** — one unit, many declared seams; **VS Code Extension
   Packs** = our *bundle*.)

4. **The contract is a declarative manifest, not the `AppConfig`.** `AppConfig` is
   Python-coupled, needs importing to read, and presumes a Django app — but addons
   can be Python-less. The contract moves to a co-located **`addon.toml`**
   (declarative, catalog-readable without executing code). `AppConfig` **demotes**
   to the optional wiring of the `python` seam (present only for `ready()`/signals).

5. **Bundle ≠ addon; distribution ≠ composition.** A **bundle** is the published
   artifact (`repo@sha`; a wheel) containing 1..N addons — `django-angee` is the
   first bundle (core + ~15 base addons), `angee-crm-suite` a third-party one. You
   **install** bundles (uv) and **enable** a subset of addons (composer). They're
   orthogonal: `installed ⊇ enabled-closure`, with dormant bundles allowed.

6. **Declare each fact once; derive the rest (DRY / SSOT).** Per-language native
   manifests stay (`pyproject`, `package.json`, `pubspec`) and own their *leaf*
   third-party deps. **Inter-addon edges are declared once** in `addon.toml`
   `depends_on`; the composer **derives** every language's inter-addon wiring from
   that graph — exactly the composer-emitted pnpm overrides we already prove,
   generalized to all ecosystems. (cf. **Pants** dependency inference, **Bazel/Nx**
   project graphs.)

7. **Installed vs enabled — Django's own split (control loop, not control data).**
   *Available* = `uv.lock` (installed bundles); *enabled* = `settings.yaml`
   `INSTALLED_APPS`, **managed by Django**. The operator owns the **lifecycle**, not
   the list: it grants Django write access to `settings.yaml`, runs `uv` installs,
   and **restarts-to-apply** (enable-vs-start, like `systemctl`). Generic operations
   — "let a service write a file," "restart a service" — so the operator never
   learns the word "addon." (cf. Django pip-vs-`INSTALLED_APPS`, systemd
   enable-vs-start, WordPress install-vs-activate.)

## Layers

| Layer | What | SSOT | Resolver |
|---|---|---|---|
| **Bundle** | published artifact, ships 1..N addons | `[project.dependencies]` → `uv.lock` | uv (transitive *bundle* deps) |
| **Addon** | the composition atom | `addon.toml` (`depends_on` = the SSOT edge) | composer (transitive *addon* deps) |
| **Contribution** | a typed seam the addon fills | inferred from layout + `addon.toml` | each seam's materializer |

## 1. Distribution — wheel carries Python + JS; `hatch-angee` is the only machinery

- The wheel ships the addon's Python **and** its co-located `web/` (and
  `templates/`, `skills/`) — via a hatch **build hook**. (Implemented for `web/`
  already; generalize to all source seams.) Editable installs skip the hook (they
  resolve from source); the hook fires only for real wheel builds (git-ref install
  or publish).
- **`hatch-angee`** — a published hatchling plugin (the `hatch-vcs` model) — is the
  *single* implementation of the build machinery; **no `hatch_build.py` per repo**.
  Every addon repo, including `django-angee`, depends on it. Default to a **wrapper
  backend** (`build-backend = "hatch_angee.build"`) so an addon repo's `pyproject`
  is ~6 lines and self-describing; plain `hatchling` + plugin tables stays the
  escape hatch for the rare repo doing something unusual.
- Minimal addon-repo `pyproject`:
  ```toml
  [build-system]
  requires = ["hatch-angee"]
  build-backend = "hatch_angee.build"
  [project]
  name = "angee-addon-crm"               # PEP 621 mandates a static dist name
  dependencies = ["django-angee>=0.2"]   # coarse BUNDLE deps (uv needs these to resolve)
  dynamic = ["version", "entry-points"]  # hatch-angee derives these
  ```

## 2. Manifests & discovery — `addon.toml` + generated entry points, no Django convention

- **`addon.toml`** marks an addon dir and declares only the non-inferable:
  ```toml
  [addon]
  depends_on = ["angee.iam"]   # often the only line; can be empty (pure convention)
  ```
  **Contributions are inferred from layout** (Cargo `autobins` style), override in
  `[contributes]` only when non-standard: `models.py`/`apps.py`→`python`,
  `web/`→`web`, `templates/`→`template`, `skills/`→`skill`, `services/`→`service`,
  `permissions.zed`/`schema.py`/`resources/`→ REBAC/GraphQL/data. `name` defaults
  from the path.
- **Discovery replaces Django's `apps.py` convention** with manifest-driven
  registration. We keep Django's app *registry* (models/migrations need it) but
  **feed it from manifests**:
  - `hatch-angee`'s **metadata hook** globs `**/addon.toml` at build and
    **generates** the `angee.addons` entry points into `entry_points.txt` — the
    **Google AutoService** pattern (mark at the definition site, generate the
    registry, never hand-maintain a roster; cf. Rust `linkme`).
  - Runtime discovery = `importlib.metadata.entry_points(group="angee.addons")`
    (unions across **all** installed wheels → cross-repo discovery is free, like
    `ServiceLoader`) **+** an `ANGEE_ADDON_DIRS` scan for local/uninstalled
    consumer addons.
  - For each addon with a `python` contribution, Angee composes its import path
    into Django's `INSTALLED_APPS` using Django's **default `AppConfig`** (no
    `apps.py` needed); the contract is read from `addon.toml`, not the AppConfig.
  - Non-Python addons never touch Django; their seams are wired by their
    materializers (codegen / operator / agent).

## 3. Dependencies — uv for bundles, `depends_on` SSOT for the rest

- **Installed** (bundles): `[project.dependencies]` → `uv.lock`. Keep exactly as-is
  — reproducible, hashed, transitive. uv owns it.
- **Inter-addon** edges: declared once in `addon.toml` `depends_on`; the composer
  derives per-language wiring (the pnpm overrides + `runtime/web/app.ts`, the
  Python `INSTALLED_APPS` order, future Flutter deps). With the catalog's
  `addon→dist` map, even the coarse Python `[project.dependencies]` become
  derivable; until then they stay explicit.

## 4. Composition — available from `uv.lock`, enabled in `settings.yaml`

This is **Django's own installed-vs-enabled split**, and the two SSOTs already (mostly)
exist:

- **Available = `uv.lock`.** The installed *bundles* (uv) determine which addons are
  *available*, enumerated via the `angee.addons` entry points `hatch-angee`
  generates (+ an `ANGEE_ADDON_DIRS` scan for local addons). This is the Django
  "what's pip-installed" / systemd "present" / WordPress "installed" tier. **It does
  not exist yet** — today the composer only knows the enabled list; the available
  enumeration is the one new piece to build.
- **Enabled = `settings.yaml` `INSTALLED_APPS`.** The roots the project composes —
  *unchanged from today*. `angee.compose.settings` → `Composer.compose_settings()` →
  `AppGraph().resolve(roots)` already turns `INSTALLED_APPS` into the `depends_on`
  closure. This is Django's `INSTALLED_APPS` / systemd `enable` / WordPress
  "activate" tier. Validation: **enabled ⊆ available**, fail-fast if you enable an
  addon whose bundle isn't in `uv.lock`.

Prior art is unanimous *and Django-native*: pip-install (available) vs
`INSTALLED_APPS` (active); `systemctl enable` (config) vs `start`/restart (run);
WordPress install vs activate; VS Code install vs enable. (The contrast —
pytest/entry-point plugins auto-activate on install — is what we deliberately reject:
explicit enabling is a feature, not friction.)

### The control loop — the operator grants management + restarts

The operator does **not** hold the enabled list — `settings.yaml` lives inside the
app **Source the operator already manages**. So enabling an addon is *modify a file
in a Source, then apply*, reusing the operator's existing gitops (it already has
`source pull`/**`push`** + `gitops topology` tracking `dirty/ahead/behind`):

```
console / angee addon enable X
  → modify settings.yaml in the app Source   (the one new capability: source *write*)
  → persist:  operator gitops push → new ref   |   Django storage   |   GitHub direct (integrate OAuth)
  → angee restart → angee build recomposes
```

- **Django manages `settings.yaml`'s `INSTALLED_APPS`** (like `systemctl enable`
  writing the symlink) and owns the addon semantics; the **operator persists +
  restarts** (enable-vs-start). Adding a bundle is the same with a `uv add` in front.
- The only new capability is **"let a managed Source's file be written"** — *not* a
  bespoke grant-and-restart subsystem. The persist + ref-pin is existing gitops; the
  apply is `angee restart`.
- Because the config is a **versioned Source**, every enable/disable is a gitops
  **commit** — auditable, revertible, surfaced by `gitops topology`. Desired state
  *is* "which ref of the app Source," and the operator reconciles to it.
- Three write paths, same SSOT (`settings.yaml`) and same restart-to-apply:
  **operator gitops** (default — centralized, full audit), **Django storage** (fast
  toggles), **GitHub direct** (app self-manages its config repo via the `integrate`
  credential). The latter two are Django self-serving via addons it already ships.
- This keeps the operator **framework-agnostic**: "write a file in a Source" +
  "restart a service" are generic — it never learns the word "addon."

So "centralize control in `angee`" resolves as: the operator owns the **lifecycle**
(grant write, install bundles, restart-to-apply); the runtime owns the
**declaration** (`settings.yaml`) and **semantics**. The deployment is reproducible
from the project Source `@ sha` (carrying `settings.yaml` + `uv.lock`) that the
operator pins.

## 5. Restart-surviving state — `runtime/` is the build SSOT, `platform.Addon` is the reflection

The runtime is **self-sufficient across restarts** (the kubelet keeps its pods
running when the API server is down) from files alone — the **build** needs no new
state store:

- `settings.yaml` (enabled), the emitted `runtime/` tree (the composed apps +
  resolved `INSTALLED_APPS`), `uv.lock`, and `django_migrations` are **already durable
  on disk** and remain the authoritative build inputs/outputs. On a bare **restart**,
  Django loads `runtime/` and comes up *as what it is*, with no operator dependency.
  The enabled list is consumed **at build, not at restart**.
- A change to `settings.yaml` is a **rebuild** event: the operator restarts →
  `angee build` recomposes → new `runtime/`. So the operator's restart *is* the
  reconcile.

**The management/marketplace surface, though, *does* want a queryable mirror —
`platform.Addon`** (✓ implemented). It is an `AngeeModel runtime=True` in the
`platform` addon, **synced on `post_migrate` from the app graph + `uv.lock` entry
points** — exactly Django's own `django_content_type` / `auth_permission` reconcile
(`create_contenttypes`), not a parallel pattern. So `platform.Addon` graduates from
*computed-each-boot* to **a model-backed resource** (REBAC, history, a normal
data-view) without becoming a second source of truth: the table is **derived, never
authored** — the files above stay authoritative, the table reflects them. (This
supersedes the first draft's `runtime/` *composition-record artifact*; a synced DB
reflection gives REBAC/history/queryability a flat file can't, while honoring the
same "derived, not hand-managed" discipline.)

**Backend ⇄ frontend stay lockstepped by construction.** One `enabled` input → one
composer pass → **both** Django's `INSTALLED_APPS` *and* `runtime/web/app.ts`'s
`composedAddons` (consumed by `createApp`). `createApp` never declares its own addon
list; it's co-emitted from the same composition, so the SPA bundle and the Django
runtime can't drift, and both are restart-surviving build outputs.

## 6. Lifecycle — install ≠ enable; three states

```
catalog (installable)  ──add (uv install bundle)──▶  available  ──enable──▶  enabled (composed)
```

Commands surfaced in the Django console / `angee addon …` edit the two SSOTs, then
the operator restarts-to-apply:

| Command | Move | Mechanics |
|---|---|---|
| `search` | browse | query the catalog |
| `add` | catalog→enabled | `uv add` bundle (→ `uv.lock`) + add to `settings.yaml`; operator restarts → recompose |
| `enable` / `disable` | available⇄enabled | toggle `INSTALLED_APPS` in `settings.yaml`; operator restarts → recompose (no uv) |
| `remove` | →catalog | disable; `uv remove` the bundle if no other enabled addon needs it |
| `list` | show | entry-points over `uv.lock` = available; `INSTALLED_APPS` = enabled; mark + provenance |
| `publish` | local→catalog | `uv build` (hatch-angee) → `uv publish` to index → register in catalog |

Guards: a shared bundle is only uninstalled when its *last* enabled addon is
disabled; you cannot disable an addon an enabled addon `depends_on` (fail-fast).

## 7. Registry / marketplace — one artifact plane, a thin catalog

- **Artifact plane = a PEP 503 / PyPI-compatible index** (the wheels). uv installs
  natively. Pluggable like `helm repo add` / Terraform registry config / `brew tap`
  (public + private + local + `git+…@sha` for dev). Don't reinvent resolution.
- **Discovery plane = a thin Angee catalog** over it (metadata from `addon.toml` +
  marketplace UX: version, compat, `repo@sha`, signature, screenshots). MVP: a
  git-backed catalog (Homebrew-tap / Helm-index / pkgx-pantry shape); grows into a
  hosted API + site (OpenVSX / Artifact Hub) without changing the artifact plane.
- **Trust** is an Angee advantage: the contract is explicit, so the console can
  show *before install* what an addon adds (models, permissions, MCP tools,
  framework compat), plus signed wheels (PEP 740) + publisher identity.
- The **marketplace is owned by a `platform_integrate_vcs` addon** — it extends
  `platform.Addon` (§5) with the **installable** tier: addons *known from VCS sources*
  (`repo@sha` provenance) but **not necessarily materialised** (not installed/built).
  `platform` only ever holds *materialised* rows (available/enabled, the local
  reflection); `platform_integrate_vcs` adds the unmaterialised catalog rows via a
  second reconciler over the same table (the content-type pattern with a *remote*
  source). It composes into the platform console; the marketplace is a normal
  data-view over the unified `Addon` view, filtered by state.

## Open decisions

- **Framework granularity:** ship coarse (`django-angee` bundle) now; per-base-addon
  wheels later for true à-la-carte. Same registry/mechanism, finer units.
- **Marker name:** `addon.toml` (locked over `manifest.toml`/`angee.toml`).
- **`hatch-angee` form:** ✓ resolved — wrapper backend (`hatch_angee.build`) is the
  default and only front door; published to PyPI. Plain `hatchling` + plugin tables
  remains a latent escape hatch.
- **Available-set enumerator:** ✓ built — `available_addons()` reads the `angee.addons`
  entry points over `uv.lock`'s bundles (+ `ANGEE_ADDON_DIRS`), and `platform.Addon`
  persists the result (§5).
- **`settings.yaml` deps derivation:** keep coarse bundle deps explicit, or derive
  them from `enabled` via the catalog's `addon→dist` map once it exists.
- **`uv pip compile`** for reproducible container image builds (the one borrowable
  idea from the uv+pex monorepo article; pex itself is orthogonal to ASGI serving).

## Prior art (load-bearing references)

Odoo modules · VS Code `contributes` + Extension Packs · JupyterLab prebuilt vs
source extensions · Dash/Streamlit JS-in-wheel · Cargo workspaces + auto-target
discovery · Go directory-as-package + blank-import registration · Rust `linkme`
distributed slice · Google AutoService (generate `META-INF/services`) · Python
entry-points / Java `ServiceLoader` · Helm repos + Artifact Hub · Terraform registry
protocol · OpenVSX · pkgx pantry · Pants dependency inference · Kubernetes
desired/actual + kubelet node-local state.
