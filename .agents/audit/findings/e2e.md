# @angee/e2e — structural audit ledger

Scope: packages/e2e (harness) + examples/notes-angee/e2e (the doc-designated
"worked example a consumer copies", docs/testing/e2e.md:58). Read-only.

- id: e2e-001
  loc: examples/notes-angee/e2e/pages/notes-page.ts:1
  category: docs-code-drift
  severity: high
  rule: docs/testing/e2e.md:50-53 — "Import these from `@angee/e2e`, never from `@playwright/test` directly … avoids the dual-instance trap"; AGENTS.md DRY "Keep the foundation clean so the code people copy is the code we want them to write."
  finding: The reference Page Object imports `expect` from `@playwright/test` directly, the exact import the harness contract forbids; copied into every consumer addon.
  fix: Import `expect` from `@angee/e2e` (re-export `Locator`/`Page` types there too so pages never reach `@playwright/test`).
  status: fixed

- id: e2e-002
  loc: packages/e2e/src/graphql.ts:5
  category: dead-code
  severity: medium
  rule: AGENTS.md Constitution "Prefer deletion to abstraction. Add an abstraction only when it removes real duplication."; Mechanical Overrides "Before structural refactors, remove dead code first."
  finding: `CONSOLE_GRAPHQL_PATH` is exported (index.ts:8) but no consumer constructs a console-path GraphQLClient — both the `api` fixture and `loginViaApi` bind to the public path; unearned/speculative export.
  fix: Delete `CONSOLE_GRAPHQL_PATH` and its re-export until a console-schema e2e flow actually needs it.
  status: fixed

- id: e2e-003
  loc: packages/e2e/src/config.ts:49
  category: lifted-code
  severity: low
  rule: docs/guidelines.md "Avoid Red Flags — Spaghetti / hidden dependencies"; AGENTS.md "Make extension mechanical: explicit owners, fail-fast collisions."
  finding: `...options.overrides` is a flat top-level spread, so a consumer passing `overrides: { use: {...} }` or `overrides: { projects: [...] }` silently clobbers the framework `baseURL` and the `setup` dependency rather than merging — a latent extension trap (no consumer triggers it today; docstring says "shallow").
  fix: Deep-merge `use`/`projects` (or constrain `overrides` to a documented safe subset like `{ workers; retries; timeout }`) so framework-owned seams cannot be dropped by extension.
  status: fixed

# Adjudicated NOT violations
# - graphql.ts:46 `variables: Record<string, unknown>` (scanner candidate):
#   correct idiom. GraphQL variables are an open string-keyed JSON map; `unknown`
#   (not `any`) is the right element type and mirrors the stack owner urql /
#   @angee/sdk graphql-client. Not a missing explicit type.
# - auth.ts LOGIN_MUTATION `ok` field: verified against emitted SDL
#   (public.graphql:294 type LoginPayload { ok: Boolean! }) — real contract, not drift.
