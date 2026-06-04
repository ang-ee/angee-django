# Audit resolution log — "fix all findings" sweep

Owner-first fix loop over the addon-by-addon decomposition audit + the A1–A10 /
B1–B4 pattern-standardization plan. Rhythm: Codex per unit on disjoint paths
(typecheck-only verify), Claude runs the full gate + commits each unit.

## Committed

| Commit    | Unit / findings                                                        |
|-----------|------------------------------------------------------------------------|
| `2ec6c29` | B1–B4 — docs/stack.md library ownership reconciled                     |
| `d618b33` | base-002 — deletion preview builders folded onto owners                |
| `897324a*`| A2 — crud() elevated-write extension (`897324a`→`897373…`, see git)    |
| `780324d` | iam-001 — AccountStatus rollup/precedence/from_* on the enum           |
| `1a5f2e0` | resources + base discovery/apps naming                                 |
| `e2c39c3` | e2e-001..003                                                           |
| `ffa3e0e` | sdk auth.test — roles inert assertion                                  |
| `c026be8` | iam-002..007, A3, A4 — OidcIdentityResolver + StateField + find_spec   |
| `313fae6` | pkgbase-002..009, A7/A8/A11 — one ListView, DataViewState, Filter      |
| `080f76f` | integrate-001..004, notes-001..004, A4 — base shims, typed sigs        |
| `429b598` | operator-001, A10 — graphql import hoist; daemon settings-pure         |
| `e25dba7` | operator-002..005 — one section table, drop dead files + frame fwd     |
| `3838048` | web-001/002, A9, storybook-001..004, pkgbase-010 — PassthroughChrome,  |
|           |   real ListView story, date-fns, story DRY                              |
| `1e2cbb1` | A1 — pluggable auth on the SDK client factory; operator drops its fork |

All A1–A11, B1–B4, and every per-unit decomposition finding are resolved.

## Consciously NOT fixed (find-the-owner says don't)

- **pkgbase-001** (residual) — the hardcoded `STATUS_ORDER` smell IS gone (list
  view now keys off `column.tone` order). What remains is deciding *which* row
  field is the "title" and which is the "status" for a generic list row. That is
  schema/addon-owned metadata the backend does not emit yet; a frontend heuristic
  would be a guess and would violate find-the-owner. TODO left in `packages/base`.
  **Real owner / follow-up:** the GraphQL schema should annotate title/status
  fields (a backend contract change), then the list view reads the annotation.

## Gate (backend, from repo root, UV_CACHE_DIR=.uv-cache)

angee build → makemigrations --check → migrate → rebac sync → resources load →
schema --check → pytest → ruff → mypy. FE: `pnpm -r run typecheck` + per-package
vitest + `cd src/angee/operator/web && npx tsc --noEmit`.
