# Notes + Auth Lift — consolidated plan (single source of truth)

> **STATUS: backend DONE — kept as the single source of truth the active frontend
> lift (`frontend-stack-upgrade-popover-dry.md`) still cites.** The backend/auth
> surface shipped (see `/CHANGELOG.md`); retire this file together with the
> frontend lift when that completes.

This is the **one** plan. It supersedes and replaces every prior `.agents/plans/*.md`
(M1 `lift-auth-graphql`, M2 `clean-build-schema-emit`/`m2-aggregates`/`m2-dev-stack`,
the `2026-05-30-*` review docs, `m3-frontend`/`m3-frontend-production`, and the interim
`lift-notes-auth-from-p1`/`notes-e2e`). Their forward work is folded in below; their
history lives in git. The live execution log stays in
`.agents/notes/lift-auth-graphql/STATE.md`; the concrete lib-wiring recipe in
`.agents/notes/lift-auth-graphql/RECIPE.md`.

**North star:** `example/notes` **and** auth, end-to-end, faithful to the prototype's
rich UX, REBAC-enforced, **verified by Playwright e2e** — not a weak copy. Less code is
better; defer to the stack; reconstruct, never copy; no provenance anywhere.

Reference prototype (read-only): `/Users/alexis/Work/angee/angee-django-p1`
(STATE/m3 cite a `fyltr/` path — confirm which checkout is canonical).

---

## 0. Status ledger

- ✅ **M1 — auth + REBAC↔GraphQL seams** (committed). `iam.User` (composed swappable,
  label `iam`), `AngeeNode`/public ids, universal REBAC extensions, denial codes
  (`UNAUTHENTICATED`/`PERMISSION_DENIED`), `login`/`logout`/`currentUser`, REBAC-scoped
  notes read-side + count aggregate, WS `noteChanged` gating, addon GraphQL in `schema.py`.
- ✅ **M2 — one-boot compose + full SDL + dev stack** (committed). Emit-then-adopt (no
  `ANGEE_BUILD` flag), `StateField` enums, filters/order, `noteAggregate`, `angee dev`
  job graph.
- ✅ **M3 P1–P6 — production frontend rebuild** (merged at `43664c9`). SDK on the offset
  contract; `@angee/base` rebuilt (foundation/tokens, 22 primitives, createApp, chrome,
  shells, chatter); login screen, notes list, note record; CodeMirror markdown editor;
  Activity tab on `noteRevisions`; record pager + view switcher.
- 🔄 **Working tree:** `FormView.tsx` changed-field-diff on submit (send only changed
  fields). Reconcile with Phase B5 (unsaved guard) — do not clobber it.
- ⬜ **Pending:** everything in §3.

---

## 1. Locked constraints (apply to all work)

- **No codegen. Hand-written `schema.py`** per addon, with `crud()`
  (`src/angee/base/graphql/crud.py`) + `changes()`
  (`src/angee/base/graphql/subscriptions.py`). Do **not** lift p1's
  `schema_emitter`/`crud_emitter`/declarative-Meta machinery.
- **Code is the source of truth; `docs/` is out of sync** — verify against `src/angee/`
  and addon code. Reconcile docs only opportunistically.
- **Pagination = OFFSET** (architect chose jump-to-page) — not relay cursors.
- **iam.User** (label `iam`), `contrib.auth` stays installed, **no sys.modules shim**.
- **Lift hygiene:** reconstruct (no file byte-identical to a prototype file), strip ALL
  provenance, land each fact at its owning level, reuse local primitives.
- **Find the owner:** framework-generic behavior → `@angee/base`/`@angee/sdk`/`src/angee/*`;
  notes-specifics → the notes consumer addon.
- **Per-phase gate** (§4) is green before commit; new deps carry a `docs/stack.md` owner
  row in the same change.

## 2. Open decisions (architect call)

- **D-A — view-state store: `nuqs` vs TanStack Router search.** m3 added `nuqs` "for
  fidelity"; the M3 P1-review HIGH findings (stale-closure batch drop; selection spamming
  Back) + `docs/stack.md`'s nuqs row point at **Router-native** as the single owner.
  Persistence (`?view=board` surviving reload) works either way — this is about who owns
  the serializer. **Recommend Router-native.** Decide before A1/B2 touch the data-view model.
- **D-B — `word_count` column.** Keep it a `@property` (current is cleaner; live count is a
  frontend concern, group total is a client-side page sum). Promote to a stored column
  **only** if server-side filter/sort/aggregate on word count is wanted. **Default: keep
  property; defer.**
- **D-C — `currentUser.roleRefs`.** F6 deferred role refs in M1; Phase C needs them so the
  client `hasRole` is real. Confirm the role surface (REBAC role tuples → a small
  `roleRefs` field on `UserType`).

---

## 3. Remaining work (dependency-ordered)

### Phase A — Finish M3 (close the production rebuild)

**A1 — M3 P1 review fixes** (P1 over-built; fix before B consumes these files):
- HIGH: collapse the parallel vocabulary — Element-DSL `ColumnDescriptor`/`FieldDescriptor`/
  `PageFieldKind` must be the ONE owner; delete the lean `ListColumn`/`FormField`/`FieldKind`
  when ListView/FormView consume the DSL.
- HIGH: `DataViewProvider.dispatch` stale-closure batch drop → single `useReducer` synced to
  the URL in one effect (or functional `setQueryValues(prev=>…)` emitting only changed keys).
  (Resolves with D-A.)
- HIGH: selection is URL-synced under `history:"push"` (checkbox toggles spam Back) → hold
  selection in local state; drop `view` from pushed history.
- MEDIUM: `statusbar` hardcodes the status ladder → drive steps from `field.options` (same as
  `select.tsx`); ties into B6 (status tones).
- MEDIUM: mount `DataViewProvider`/`useDataView` from the ListView page that consumes it.
- MEDIUM: page-size default `50` duplicated → reuse the SDK owner (`DEFAULT_PAGE_SIZE`).
- LOW: `userRef` guesses shape → pass a resolved label; `selection`→`selectedIds` (name
  collides with the SDK GraphQL `selection`); `widgetLabel` → `widgets/label.ts`; DSL
  fail-fast on duplicate `field`/`name`/`id`; validate `dataViewFilterFromUnknown` at the SDK
  filter boundary.

**A2 — M3 P7 gate + acceptance.** Full gate (§4) + `architecture-reviewer`/`react-reviewer`
passes; browser walkthrough covering the subcases STATE flagged as unproven: create / edit /
status-transition and **multi-page** list paging; confirm live `noteChanged` cross-client.

### Phase B — Notes UX richness (the "not a weak copy" bar)

Each row = a gap from the two-repo comparison; owner = where the behavior lives; e2e = the
spec in Phase D that proves it.

| # | Gap (current → target) | Owner / files | e2e |
|---|---|---|---|
| B1 | **Live word count** (server property, refresh-on-save) → updates per keystroke | **Seam:** expose the live form draft to widgets in `FormView` (`packages/base/src/views/FormView.tsx`) — today each widget gets only its own `api.state.value`; add a form context + `useFormDraft()` (reconstruct p1's `useFormState()`). **Widget:** notes `word-counter` reads `values.body` (`examples/notes-angee/src/example/notes/web/…`). | `notes-word-count` |
| B2 | **Kanban + DnD** (plain card grid) → per-status columns, drag-to-set-status | `packages/base/src/views/BoardView.tsx` + `@dnd-kit/{core,sortable}`; drop writes status via the update mutation. Honor D-A for view persistence. | `notes-views` |
| B3 | **Star control** (backend `is_starred` + Starred tab exist; no UI control) → toggle on row + form header | notes web; writes `is_starred` via `noteUpdate` (thin `star` verb optional, B7). Makes the Starred tab reachable. | `notes-star-archive` |
| B4 | **Full-text search** (title-only; ⌘K stub) → title+body | expose `body` on `NoteFilter` (`examples/notes-angee/src/example/notes/schema.py`); wire ⌘K Spotlight to real results. | `notes-search` |
| B5 | **Unsaved-changes guard** (none) → leave-while-dirty prompt | `FormView` — reconstruct p1's `FormUnsavedChangesGuard` on the Router blocker; **reconcile with the working-tree changed-field-diff edit.** | `notes-form-dirty` |
| B6 | **status tones** (badge colors) | `StateField`/field-meta carries per-choice tone to the badge cell; consumer declares values+tones. Pairs with A1's statusbar-from-`field.options`. | `notes-list` |
| B7 | **Custom verbs** (none) → optional `archive`/`star` hand-written `@mutation`s | `examples/notes-angee/src/example/notes/schema.py`. `share` (writes a REBAC tuple) **deferred**. | `notes-star-archive` |

**Keep (do not regress):** opaque `createdBy`/`updatedBy` scalar ids (no nested user);
`word_count` as `@property` (D-B); dual history+reversion.

### Phase C — Auth: login & roles + preferences (the chosen scope)

**In scope:** full login & roles UX + a real preferences page. **Deferred:** IAM admin
console, machine identity (`Service`/`ApiKey`), impersonation, Permissions-Hub GraphQL, OIDC
provider wiring (keep an empty SSO slot).

Backend (`src/angee/iam/`, hand-written):
- **C1** roleRefs on `currentUser` (`iam/schema.py`) resolving REBAC role tuples (D-C).
- **C2** superuser ↔ `angee/role:admin` sync signal (so admin truly has the role C1 reports).
- **C3** `preferences` JSONField on `User` + `userUpdatePreferences` mutation (optional `avatar`).
- **C4** `changePassword` (self) — optional, include if the prefs page hosts it.

Frontend:
- **C5** real `hasRole` from `currentUser` roleRefs — replace the hardcoded `false` in
  `packages/sdk/src/auth.ts`.
- **C6** `RoleGate` + route `roles` (extend `define-addon` `AddonRoute` + `createApp`); unmet
  role renders forbidden **inside** the shell; filter menus by `hasRole`. (Client gates are
  UX only; server stays the authz boundary.)
- **C7** extensible user menu (`UserMenuItem[]`; addons anchor Profile/Preferences at
  `parent:"user"`; "Sign out" appended) — `packages/base/src/chrome/UserMenu.tsx`.
- **C8** Preferences page: route hosting the theme picker + (optional) password change,
  persisting via `userUpdatePreferences` (C3).
- **C9** working theme: `@angee/sdk` `useTheme()` sets `<html data-theme>` + persists
  (localStorage now; backend prefs via C3); theme-picker widget in `@angee/base`. (Dark
  tokens already exist; nothing sets `data-theme` today.)
- **C10** multi-method login orchestration into `packages/base/src/auth/`: primary-method +
  "other options" collapse, empty SSO slot, reset-link slot, vendor link, version badge, i18n
  with per-error-code messages.
- **C11** CSRF provider + REBAC zookie clearing on login/logout in `@angee/sdk` (today only
  the urql client is reset).

### Phase D — E2E acceptance suite (authored FIRST; the lift's definition of done)

Harness: `@angee/e2e` (`packages/e2e/src`) + `examples/notes-angee/e2e/`. App under test via
`angee dev`; seeded alice/bob; role `storageState`; GraphQL `api` fixture; `PageObject` base.
Selectors accessibility-first; add `data-testid` **only** where state isn't otherwise stable
(word-count readout, view-switch, board columns, star toggle, theme options, user-menu) — and
land the id in the same phase as its feature.

POMs to add (`examples/notes-angee/e2e/pages/`): extend `notes-page.ts`
(viewSwitch/searchBox/groupByChip/boardColumn/rowByTitle/starToggle); new `note-form-page.ts`
(editor/toolbar/wordCount/statusStep/save/discard/tagInput/modeToggle); new `app-chrome.ts`
(user menu, preferences, `htmlTheme()`).

Spec matrix (current behavior today → target):

| Spec | Feature | Cases | Now | Closes in |
|---|---|---|---|---|
| `notes-editor` | toolbar + markdown | bold/italic/code/list/quote/link transform body; source⇄preview; ⌘B | 🟢 | A2 (lock) |
| `notes-word-count` | **live word count** | readout increments while typing, before save; matches body count | 🔴 | B1 |
| `notes-views` | **toggle + kanban** | list↔board; persists reload + back/forward; per-status columns; drag Draft→Active updates status | 🟡 | B2 (+D-A) |
| `notes-list` | list/sort/group | columns; sort cycles+reorders; group-by Status headers+counts+word total; column chooser | 🟢 | A2 (lock) |
| `notes-search` | **search+filter** | title substring; **body text**; status facet; saved favorite | 🟡 | B4 |
| `notes-star-archive` | **star+archive** | star toggles `is_starred` (UI+`api`+Starred tab); archive→Archive tab; bulk w/ confirm | 🔴/🟡 | B3/B7 |
| `notes-form-dirty` | save/dirty/guard | Save only-when-dirty; persists changed fields; Discard reverts; **leave-while-dirty prompts** | 🟢/🔴 | B5 |
| `notes-revisions` | revisions/Activity | edit body → Activity shows new revision + relative time; count badge++ | 🟢 | A2 (lock) |
| `preferences-theme` | **prefs+theme** | user menu→Preferences; dark → `<html data-theme="dark">`; reload persists | 🔴 | C8/C9 |
| `auth-login-roles` | **login+roles** | UI login → /notes; demo-creds slot; route needing a missing role shows forbidden in-shell; admin-only menu item hidden from a normal user | 🟢/🔴 | C5/C6 |

🟢 lock now · 🟡 partial · 🔴 written red. Red specs land with their phase (or now as
`test.fixme` with `// unblocks: <phase>`); the fixme list is the remaining-lift ledger. Assert
**state** (editor value, `aria-pressed`, `data-theme`, reload+URL, the `api` fixture), never
pixels; no sleeps (`expect.poll` for the live count). Mutating tests create unique notes via
`api` and clean up. CI job is not yet wired (`docs/testing/e2e.md`) — add it once the red
specs are green; don't gate CI on `fixme`s.

### Phase E — Backend hardening backlog (carried over; re-verify vs HEAD, fix if still open)

STATE shows M1/M2/round-2 landed; several items below may already be resolved — **verify in
code before fixing**, don't assume from these notes.

**Audit pass (2026-06-01, this session) — resolved / confirmed:**
- ✅ **Aggregate field-gate leak — FIXED.** Dropped `is_starred` from the aggregate
  `group_by_fields` in `examples/.../notes/schema.py` (kept `status`/`updated_at`, both
  non-gated); documented the `on_field_deny("allow")` safety boundary in `_rebac_scoped`'s
  docstring + the aggregate comment; added regression
  `test_owner_gated_flag_is_not_an_aggregate_group_by_axis` (the gated flag is no longer a
  valid group-by enum value). Gate green; SDL re-emitted.
- ✅ **F5 rationale lifted** into `src/angee/base/apps.py` `schema_module` docstring (why
  `schema.py` not `graphql.py` — avoids shadowing the `graphql` core package).
- ✅ **round-2 `allow_non_dev` is NOT dead** — it is live/used (`resources/managers.py:67`
  DEMO guard). Finding resolved; dropped from the close-out list.
- ⚠ **REBAC actor-context test bleed (NEW finding).** In `test_iam_graphql.py`, an
  authenticated session in one `TransactionTestCase` test bled the REBAC actor into the
  *next* test's request (a bob-login made the following admin query scope to bob → "3 != 58").
  The actor contextvar / zookie is not reset between tests. Worth a real fix (reset
  `current_actor`/zookie per test or in `ActorMiddleware` teardown). NOTE:
  `test_platform_admin_sees_every_users_notes` + the `self.bob` setUp line in that file are
  **uncommitted WIP not authored here** — left for their owner.
- 🧹 **`AngeeConnection`/`Connection`** (`src/angee/base/graphql/node.py`, exported from
  `graphql/__init__.py`) appears **unused since the offset-pagination switch** — verify no
  consumer (incl. the SDK contract), then delete; the M1 review also disputes its
  `Meta.ordering` premise.
- 📌 **PyYAML owner row** still missing from `docs/stack.md` (YAML resources use it).
- 🛠 **Tooling: the `.venv` interpreter points at the old `…/fyltr/angee-django/.venv`** (the
  repo moved); `uv run mypy`/`uv run pytest` fail to spawn. Use `uv run python -m mypy/pytest`
  or rebuild the env (`uv sync`).

M1 adversarial review (verify against HEAD):
- **UserManager REBAC bypass** (`src/angee/iam/models.py`): `get()`/`_is_session_lookup` route
  pk lookups through `system_context`, bypassing REBAC framework-wide; **confirmed not
  mirrored on async `aget()`** (no `aget` override). Fix p1-style: the **auth backend** owns
  the no-actor session/credential fetch (`get_user`/`aget_user` wrap `system_context`), or
  give `iam.User` a plain `BaseUserManager`. (Earlier review called the current narrow form
  "fine" — architect to confirm whether to keep or fix.)
- **crud delete id surface** (`src/angee/base/graphql/crud.py`): delete, detail, and update
  now take raw public sqid IDs through the shared Angee GraphQL boundary.
- **`_merge_root` copy.copy** (`src/angee/base/graphql/schema.py`): builds each named schema's
  root from per-schema surface **instances** instead of shallow-copying to undo relay's
  in-place field mutation.

Followups §1 (re-verify; fix if open):
- `DeletionPreview` crash on `SET_NULL`/`SET_DEFAULT` cascades (`base/deletion.py`): count by
  `field.model`, catch `RestrictedError`, include `collector.fast_deletes`; non-vacuous tests.
- Wrap crud delete-preview + delete in one `transaction.atomic()`.
- `angee clean` leaves the runtime uncleanable (sentinel deleted while migrations preserved).
- Mass-assignment: structured-row `fields` can override reserved `_xref`.
- `resources validate` only checks headers → run a real dry-run import, or rename.
- N+1 in resource loading (preload ledger/target/xref per run).
- Pin `REBAC_STRICT_MODE=True` in `compose_defaults` (Angee-owned fail-closed).
- `fetch_url` follows redirects without re-validating scheme/host.
- Quality: `signals.py`→`ChangePublisher` class; `_models_source` string-concat;
  `import_row` decomposition; `_json_safe` strict; explicit adoption key; `RevisionMixin`
  typing; parenthesize `except (TypeError, ValueError)`; misc naming/owner-row gaps.

Round-2 (STATE indicates landed — confirm green, then close): xref label-alias
canonicalization; WS actor dead-code (remove or bracket honestly); cross-model
`(source_addon, xref)` reuse fail-fast; non-vacuous fast-delete/SET_NULL tests; `schema --check`
bootstrap doc sequence; layering doc "import" wording; PyYAML owner row. (`allow_non_dev` — done.)

---

## 4. Sequencing & gates

1. **Author Phase D** → red acceptance ledger.
2. **Phase A** (M3 finish) → flips `notes-editor`/`notes-list`/`notes-revisions` green; sets up
   the seams B needs. Resolve **D-A** here.
3. **Phase B** (richness) folds into the same view files where it overlaps A → flips
   `notes-word-count`/`notes-views`/`notes-search`/`notes-star-archive`/`notes-form-dirty`.
4. **Phase C** (auth roles/prefs) → flips `preferences-theme`/`auth-login-roles`. Resolve D-C.
5. **Phase E** — correctness items (DeletionPreview, atomic delete, REBAC strict, UserManager
   async bypass, actor-context test bleed) before any release; quality items opportunistically.
   (Aggregate field-gate leak — fixed this pass.)

**Per-phase gate (green before commit):**
```sh
pnpm -r typecheck && pnpm -r test && pnpm run build          # frontend phases
uv run ruff check . --no-cache && uv run mypy src/ && uv run pytest   # backend phases
uv run examples/notes-angee/manage.py angee build --check
uv run examples/notes-angee/manage.py schema && uv run examples/notes-angee/manage.py schema --check
# + the Phase-D e2e subset green against `angee dev`; reviewer passes
#   (architecture-reviewer / react-reviewer / django-reviewer); live screenshot/smoke.
```
**Definition of done:** every Phase-D spec green (no remaining `test.fixme`); gates green;
reviewer findings fixed; no provenance; new deps owner-rowed.

## 5. Pointers (kept, not duplicated here)

- Execution log / compaction lifeline: `.agents/notes/lift-auth-graphql/STATE.md`
- Lib-wiring recipe + locked symbols: `.agents/notes/lift-auth-graphql/RECIPE.md`
- Reviewer personas: `.agents/agents/{architecture-reviewer,django-reviewer,react-reviewer}.md`
- Six-pass reports: `.agents/notes/final-review/*`
- p1 reference: `/Users/alexis/Work/angee/angee-django-p1`
