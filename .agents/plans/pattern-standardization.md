# Pattern & Library Standardization Plan

Fixes every inconsistency in `.agents/audit/patterns/CONSISTENCY.md` (items A1–A10,
B1–B4). Each item: **canon** (the one shape we standardize on), **drift** (what's
there now, with file:line), **fix**, **owning unit**, **risk**, and any **decision**
to confirm first. The ✦ items also appear in the decomposition audit
(`.agents/audit/findings/*.md`), so they fold into the **same owner-first fix loop**
in `.agents/plans/codebase-audit.md` — one Codex pass per unit clears both.

Rule for the whole plan: **standardization is structure, not behavior.** Any item
that would change behavior stops and surfaces it. Each item ships gated + reviewed.

---

## Bucket B — reconcile `stack.md` with reality (do FIRST: cheap, doc/deps only, 1 commit)

- **B1 — phantom locked rows → Proposed.** `django-ninja`, `pydantic`, `python-magic`
  sit in the LOCKED Backend table but are undeclared in `pyproject.toml` and imported
  nowhere. Move all three to "Proposed, Not Locked." *(docs only)*
- **B2 — add the `pyyaml` row.** It's declared + used (YAML resource files) with no
  stack.md row, which the Change Policy forbids. Add a Backend row
  (`pyyaml | YAML resource declarations | resource loader parsing`). *(docs only)*
- **B3 — fix the `strawberry-django-aggregates` claim.** The row says Angee adds
  framework "wiring to model metadata," but the only caller is the example addon
  (`AggregateBuilder` in `examples/.../notes/schema.py`). Reword the "Angee adds" cell
  to "addon-level `AggregateBuilder` wiring" (it's a per-addon tool, not framework glue).
  *(docs only)*
- **B4 — prune unused deps + branded-types decision.** (a) Remove 5 declared-but-unused
  `@angee/base` deps (`@dnd-kit/core`, `@dnd-kit/sortable`, `@floating-ui/react-dom`,
  `valibot`, `use-debounce`) **or** confirm they're imminent; (b) move 6 aspirational
  stack.md rows with no dep+code (`@xyflow/react`, `react-dropzone`,
  `react-json-view-lite`, `ansi-to-react`, `simple-icons`, `@lobehub/icons`) to Proposed;
  (c) **DECISION** — branded boundary types are promised by stack.md but every id crosses
  as bare `string`: either adopt a lightweight branded `Id`/`Sqid` type at the SDK
  boundary, or drop the claim. *(deps + docs; branded-types is code if adopted)*

Verify B: `uv sync` / `pnpm install` clean, `manage.py schema --check`, no import breaks.

---

## Bucket A — standardize a pattern (code; owner-first, fold into the fix loop)

### A1 ✦ — Operator console onto the `@angee/sdk` seams  *(unit: operator + sdk)*
- **Canon:** the SDK's `useDocumentQuery` read seam, authored-mutation runner,
  `stable-deps`, `useDataViewSurface`; daemon types stay codegen'd from the daemon SDL.
- **Drift:** `operator/web/src/data/transport.tsx` ships its own urql client/provider +
  hand-written documents + `useOperatorSnapshot`/`useOperatorAction`; `run-action.ts`
  types payloads `Record<string,unknown>` and probes success by a passed-in `field`
  string (inspect-by-key).
- **Fix:** parameterize the SDK seams by client/endpoint, point the console at the
  daemon connection, delete the bespoke transport; success reads the typed
  `MutationResult{status}` (not a field-string probe).
- **DECISION / risk (HIGH):** the console talks to a **separate** GraphQL endpoint (the
  daemon), not the app schema — so reuse likely needs the SDK to accept a second client
  instance. Confirm that small SDK seam change is in scope before starting. Behavior must
  stay identical (same daemon ops). e2e/operator coverage required.

### A2 ✦ — `crud()` elevated mode; migrate IAM admin CRUD  *(unit: base → iam)*
- **Canon:** `crud(node, …, permission_classes=, elevated=…)` where writes run under
  `system_context` *after* the permission gate; IAM admin mutations use it.
- **Drift:** `crud()` used only by notes; `iam/schema.py` hand-rolls
  `IAMVendorMutation`/`IAMOAuthClientMutation` + `_input_values`/`_assign_values`/
  `_resolve_public_id`/`_delete_instance` because const-admin `create` is unsatisfiable
  without `system_context` (proven in #13).
- **Fix:** add an elevated-write capability to `base/graphql/crud.py` (the permission
  class is the gate; the create/update/delete resolver body runs in `system_context`),
  then replace the IAM hand-rolled surface with `crud(VendorType/OAuthClientType, …,
  elevated=…)` and delete the helpers.
- **DECISION:** the elevated API shape. Recommend `crud(..., write_context="system",
  reason="…")` wrapping the strawberry-django mutation resolver in `system_context`,
  ordered after `permission_classes`. Confirm before building (it's a framework change —
  highest bar). Behavior-preserving (same gate, same elevated write).

### A3 ✦ — enum columns → `StateField`  *(unit: iam, resources)*
- **Canon:** `StateField(choices_enum=…)`.
- **Drift:** `Credential.kind` (`iam/models.py:709`) and `Resource.tier`
  (`resources/models.py:24`) use bare `CharField(choices=Enum.choices)` while sibling
  `status` columns use `StateField`.
- **Fix:** convert both to `StateField`; check call sites (StateField's descriptor
  returns the enum, not a bare str) and the generated migration is benign.
- **Leave alone:** `AccountStatus`(TextChoices, persisted) and `StateFlow`(StrEnum, cache)
  are correct — not part of this item.
- **Risk:** low; generates a migration (field deconstruct changes) — verify drift + gate.

### A4 — one Manager/QuerySet shape  *(unit: base-guideline → iam, integrate, operator)*
- **Canon (DECISION):** chainable scoped *reads* → queryset methods via
  `Manager.from_queryset`; *factories/mutations* (`link`, `upsert_for_user`,
  `deliver_event`, `create` overrides) stay manager methods. Recommend documenting this
  split in `docs/backend/guidelines.md`.
- **Drift:** 5 RebacManager subclasses carry read+write methods inline; only `resources`
  uses `from_queryset`.
- **Fix:** after the canon is documented, migrate the read-shaped methods to a
  `*QuerySet` exposed via `from_queryset`; leave factories on the manager.
- **Risk:** low but wide; pure refactor. Confirm the canon first.

### A5 ✦ — collapse the stable-deps memo idiom to one home  *(unit: sdk)*
- **Canon:** `sdk/src/stable-deps.ts` `useStableVariables` (its self-declared single home).
- **Drift:** byte-identical copy (`authored-hooks.ts:13`), inline `JSON.stringify`/`join`
  (`resource-hooks.ts:101,279`), sorted-key `stableSerialize` (`grouped-list.tsx:959`).
- **Fix:** if the sorted-key variant is genuinely stronger, fold it into `stable-deps`
  as the one impl; delete the other 3, import the owner.
- **Risk:** low; vitest covers hooks.

### A6 ✦ — derive GraphQL names from the contract, not heuristics  *(unit: sdk)*
- **Canon:** names come from the generated SDL/codegen (the SDL already ships them).
- **Drift:** `sdk/src/selection.ts` hand-rolls `pluralize` + first-letter casing (comment
  admits irregular plurals break); `relay-invalidation.tsx` inline lowercase.
- **Fix:** read names off the typed document/codegen; pass explicit names where the
  runtime builder needs them.
- **DECISION / investigate:** the sdk audit held this at low confidence (sdk-006) — the
  pluralize may be a deliberate runtime-document-builder tradeoff. Confirm it's
  replaceable by the contract before migrating; if genuinely needed at runtime, document
  *why* instead.

### A7 ✦ — one list view (`ListView` = `GroupListView` minus grouping)  *(unit: packages/base)*
- **Canon:** a single component; grouping/board are branches.
- **Drift:** `views/{ListView.tsx, group-list-view.tsx, list-internals.tsx}` duplicate
  ~80 lines of toolbar/selection/error/dialog shell (GroupListView is a strict superset).
- **Fix:** collapse onto one implementation. **Couple with** the decomposition fixes
  pkgbase-002/003/004 (the missing `DataView`/`Filter` owner) — same files, do together.
- **Risk:** medium-high (core view); needs vitest + the grouped-list/Kanban e2e (already
  exists) green.

### A8 — one URL-state owner for the active tab  *(unit: packages/base)*
- **Canon (DECISION):** nuqs owns chrome tab state (stack.md: "remaining chrome query
  state such as top-menu tabs"); Router owns route search. Recommend `tab` → nuqs only,
  with the dataView filter derived from one source.
- **Drift:** `chrome/TopMenu.tsx:94-97` writes `tab` to **both** nuqs and Router → can
  desync.
- **Fix:** single-write `tab` via nuqs; remove the Router dual-write.
- **Risk:** low-medium (URL behavior; list/board e2e across logins touches it). Confirm
  the owner.

### A9 — relative-time via date-fns everywhere  *(unit: web)*
- **Canon:** date-fns `formatDistanceToNow` (stack.md assigns relative-time to date-fns;
  `list-internals.tsx` already uses it).
- **Drift:** `NotePage.tsx:354` hand-rolls `Intl.RelativeTimeFormat`.
- **Fix:** replace with the date-fns path / the shared timestamp widget.
- **Risk:** low.

### A10 — read env via settings, not `os.environ`  *(unit: operator)*
- **Canon:** host owns env; code reads Django settings.
- **Drift:** `OperatorDaemon._setting` is the only code reading `os.environ` directly.
- **Fix:** route the daemon config through settings (host sets them).
- **Risk:** low; verify the daemon still resolves its config in `angee dev`.

*(A11 — `@angee/base` file-naming/barrel inconsistency, fe-patterns §4 — optional tail;
fold into the packages/base unit pass if cheap.)*

---

## Decisions to confirm before Codex starts (recommendations in **bold**)

1. **A2** crud() elevated API → **`write_context="system"` param, gated by
   `permission_classes`, write body in `system_context`.**
2. **A4** manager canon → **`from_queryset` for chainable reads; factories stay on the
   manager.**
3. **A6** GraphQL-name heuristic → **migrate to contract names** unless the runtime
   builder genuinely needs the heuristic (then document why).
4. **A8** URL `tab` owner → **nuqs.**
5. **B4** branded boundary types → **adopt a lightweight branded id type at the SDK
   boundary** (vs drop the stack.md claim).
6. **A1** operator console → confirm the SDK may gain a **second-client/endpoint** seam so
   the console can reuse it.

## Execution order (unified owner-first queue)

1. **B1–B4** — one cheap stack.md/deps commit (no code risk).
2. **base** — A2 (crud elevated capability) + decomposition base-002.
3. **resources** — A3 (`Resource.tier`) + resources decomposition (resources-001/002).
4. **iam** — A2 (migrate to crud), A3 (`Credential.kind`), A4 (managers) + iam-001/002.
5. **integrate** — A4 (manager) + integrate tail.
6. **operator (py)** — A10 (env), A1 (daemon side) + operator findings.
7. **notes** — decomposition notes-001.
8. **sdk** — A5 (stable-deps), A6 (names) + sdk findings.
9. **packages/base** — A7 (+pkgbase-002/003/004), A8 (URL), A11 + pkgbase-001/005.
10. **operator (web)** — A1 (console UI → SDK seams).
11. **web** — A9 (relative-time) + web-001.
12. **storybook, e2e** — decomposition tails (storybook-001/002, e2e-001).

Per unit: triage → Codex reconstructs (decomposition + consistency items together) →
full gate → re-audit until dry → one commit. Same loop as `codebase-audit.md`.
