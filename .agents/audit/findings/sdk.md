- id: sdk-001
  loc: packages/sdk/src/authored-hooks.ts:13
  category: dry-duplicate-primitive
  severity: high
  rule: AGENTS.md DRY "Same rule in two places: choose the owner, delete the copy"; stable-deps.ts is the audited owner of value-equality memo
  finding: authored-hooks defines a private useStableVariables byte-identical to the exported one in stable-deps.ts (the documented single home for the lint-suppressed value-memo)
  fix: delete the local copy; import useStableVariables from ./stable-deps
  status: fixed

- id: sdk-002
  loc: packages/sdk/src/aggregate-extract.ts:114
  category: dead-defensive-code
  severity: high
  rule: AGENTS.md Mechanical Overrides "Before structural refactors, remove dead code first"; Constitution "Prefer deletion to abstraction"
  finding: autoExtractGroupBy still parses a legacy `node.groups` shape (plus toBucket) that assembleGroupByDocument never emits and no schema field declares — an unreachable branch kept "for older schemas"
  fix: delete the groups branch and toBucket; keep only the `results` envelope the builder actually produces
  status: fixed

- id: sdk-003
  loc: packages/sdk/src/auth.ts:86
  category: lifted-unearned-code
  severity: medium
  rule: guidelines.md "Avoid Red Flags / code is bigger instead of smarter"; AGENTS.md "Prefer deletion to abstraction"
  finding: roles/hasRole exists in two places (currentUserToAuthState hardcodes roles:[] & hasRole:()=>false; AuthProvider re-derives hasRole from roles) but the real app path feeds the former into the latter, so the derivation is permanently inert outside Storybook
  fix: pick one owner — drop the speculative roles plumbing until REBAC actually ships roles on the user node, or compute hasRole once
  status: fixed

- id: sdk-004
  loc: packages/sdk/src/resource-hooks.ts:101
  category: dry-inconsistent-pattern
  severity: medium
  rule: AGENTS.md DRY "A fact/primitive lives once at the level that owns it"; stable-deps owns variable stabilization (used by aggregates.ts)
  finding: useResourceList hand-rolls the JSON.stringify variable-stabilization (filterKey/orderKey + eslint-disable) that stable-deps.useStableVariables already owns, so the same value-memo idiom lives in three shapes across the package
  fix: route the list variables object through useStableVariables instead of re-deriving the serialized key inline
  status: fixed

- id: sdk-005
  loc: packages/sdk/src/relay-invalidation.tsx:25
  category: find-the-owner
  severity: medium
  rule: AGENTS.md Constitution "Put behavior on the object that owns the data"; selection.ts owns type<->field-name derivation (singularFieldName)
  finding: changeSubscriptionDocument inlines `${typename.charAt(0).toLowerCase()}${typename.slice(1)}` to lowercase a type's first letter — the exact camelCasing singularFieldName performs — duplicating the field-naming rule outside its owner
  fix: expose/reuse a single first-letter-lowercase helper from selection.ts so the subscription field name derives from the same owner as every other field name
  status: fixed

- id: sdk-006
  loc: packages/sdk/src/selection.ts:113
  category: find-the-owner
  severity: medium
  rule: frontend/guidelines.md "Python ships schema and operations. TypeScript ships UX"; stack.md (strawberry-django owns field naming); module's own comment concedes irregular plurals "belong to the backend"
  finding: the SDK re-derives connection/aggregate field names from a model label with a hand-rolled English pluralize heuristic, decoding a name the SDL already ships as the source of truth (the file header calls the SDL "the source of truth")
  fix: drive field names from the contract/SDL the SDK already loads rather than guessing them; if the runtime-document-builder design is deliberate, reconcile the doc that says the backend owns naming. LOW CONFIDENCE — may be an accepted runtime-builder tradeoff
  status: fixed

- id: sdk-007
  loc: packages/sdk/src/index.ts:7
  category: unearned-public-surface
  severity: low
  rule: AGENTS.md "Make extension mechanical: explicit owners"; "Prefer deletion to abstraction"
  finding: buildSelection, printSelection, singularFieldName, pluralFieldName, aggregateFieldName, groupByFieldName, PAGE_SIZE_OPTIONS are exported from the public barrel but have zero consumers outside the SDK and its tests — internal document-builder seams leaking as public API
  fix: drop them from index.ts (keep them module-internal) unless a documented consumer needs them
  status: fixed

- id: sdk-008
  loc: packages/sdk/src/index.ts:77
  category: dry-indirection
  severity: low
  rule: AGENTS.md DRY "find the owner and remove the copy"
  finding: index re-exports AggregateBucket/GroupByResult/AggregateMeasure* from ./aggregates, which itself only re-exports them from ./aggregate-extract (the owner) — a pass-through layer that adds a second hop with no value
  fix: export those types from index directly via ./aggregate-extract and drop the re-export block in aggregates.ts
  status: fixed

- id: sdk-009
  loc: packages/sdk/bin/build-resource-types.mjs:13
  category: dead-code
  severity: low
  rule: AGENTS.md Mechanical Overrides "remove dead code first"
  finding: `here` is computed (fileURLToPath of the dir) then never used, suppressed with `void here;` at the end of the script
  fix: delete the `here` assignment and the trailing `void here;`
  status: fixed
