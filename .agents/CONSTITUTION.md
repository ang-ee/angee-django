# Constitution coverage matrix

`AGENTS.md` owns the rules. This file maps each rule to what enforces it,
in four distinct columns that must not be confused:

- **Instruction** — a skill step an agent is told to follow. It holds only
  while the skill is loaded and followed.
- **Check** — an executable gate: a system check id, a test, a build
  refusal. It holds regardless of the agent.
- **Review** — a judgment a reviewer agent or the architect makes against
  the rule. It is the only enforcement for semantics.
- **Unwired** — what nothing enforces yet.

Skills live in `skills/`; the guard is `skills/angee-design` with its owner
index `references/owners.md`; reviewers are `agents/`; the loop is
`skills/slice`. Loading: Claude Code reads `.claude/skills` (a link to
`skills/`) and may auto-invoke `angee-design` from its description, which
depends on the harness honouring descriptions; Codex
reads each skill's `agents/openai.yaml`, where only `angee-design` allows
implicit invocation. The `PreToolUse` hook `tools/owner-map-guard.py`
reminds once per session on the first source edit; it is advisory.

## Ownership and DRY

| Rule ([Constitution](../AGENTS.md#constitution), [Gate](../AGENTS.md#architecture-gate)) | Instruction | Check | Review | Unwired |
|---|---|---|---|---|
| Find the owner first | `angee-design` steps 1–4: name facts, look each up in the owner index, search two addons and sibling slots, decide the level | `tests/test_owner_index.py`: every index citation is a definition line; every public class a foundation models or fields module declares, and every addon manifest, has a row | `plan-reviewer` on the owner map when the gate requires one | semantic fit of an owner to a fact |
| One fact, one rule, one implementation | owner rules in every artifact skill name the file that owns each fact (manifest vs `AppConfig`, model vs zed vs `schema.py`, route vs menu, manifest vs resources) | composer refusal of duplicate build integrations; `test_base_layering.py::test_addons_do_not_use_a_shared_app_config_subclass`; documentation link contract | `architecture-reviewer` DRY lens; deletion list in the owner map compared with the diff | — |
| Do not reinvent the wheel; lean on upstream | `angee-design` step 5 (wire the library's native type; a forwarding wrapper is deleted); *Reuse first* in every skill lists native APIs before Angee ones | `tests/test_stack.py` (every declared dependency has a stack row; task and web deps belong to their addons); `test_layering.py` import closures (serving stays vendor-free, resource contracts projection-free) | reviewers' defer-to-stack lens; [stack.md](../docs/stack.md#backend) records why in-repo owners were chosen over libraries | — |
| Compose reusable components, never reimplement locally | *Reuse first* and *Do not* in `angee-models`, `angee-pages`, `angee-forms`: mixins, fields, standard views, registered forms, containers | `angee.E045` (`owner` without `OwnerMixin`), `E021` (hierarchy manager), `E034` (append-only), `E001`–`E005` (impl registries), `tags.E002`; `architecture-guardrails.test.ts` | `architecture-reviewer`, `react-reviewer` | a parallel implementation under a new name that no check recognises |
| No parallel implementations beside an owner | `angee-framework` step 7: required imports survive only as narrow aliases in `CORE_ADDON_BINDINGS` | `test_base_layering.py::test_core_does_not_import_folder_addons`; `test_framework_does_not_import_retired_ownership_owner` | `architecture-reviewer` | — |
| Less code; prefer deletion to abstraction | owner map carries a deletion list and a growth line; growth is legitimate only for missing behaviour or a missing owner ([DRY](../docs/guidelines.md#dont-repeat-yourself-dry)), otherwise it is the parallel-implementation signal | — | reviewers' lifted/unearned-code lens; net source growth on a redesign | — |
| Kill rot when encountered | every skill's *Sources* records doc-versus-code mismatches with the owner to change; `angee-framework` step 9 reconciles | `test_documentation_contracts.py` (links, anchors, documented commands and files) | the handover names blocked cleanup | — |
| Never quick-fix to make it run | *Verify* in every skill names false greens; `slice` requires evidence at the artifact's boundary | the checks the area requires per [checks.md](../docs/checks.md) | acceptance in the browser for changed UI | — |

## Native Django and the stack

| Rule ([Django-Native Rule](../docs/backend/guidelines.md#django-native-rule), [stack](../docs/stack.md)) | Instruction | Check | Review | Unwired |
|---|---|---|---|---|
| Coercion on fields, predicates on querysets, factories on managers, invariants as `Meta` constraints, refusals via `delete_blocker` + `pre_delete` | `angee-models` steps 3, 5, 6, 7 with their owner rules | `manage.py check`; `test_layering.py::test_blank_state_fields_use_null`; `makemigrations --check` | `django-reviewer` | — |
| No triggers, functions or `RunSQL` guards | `angee-models` *Do not* | — | `django-reviewer` | no static check for `RunSQL` or trigger DDL |
| Native Strawberry, Hasura emitters, thin resolvers | `angee-graphql` steps and resolver budget | `schema --check`; codegen | `django-reviewer` | — |
| Settings through yamlconf and environ, one owner | `angee-addon` step 4; `angee-project` | composer refusal of duplicate integrations | — | — |
| Dependency placed with its owner, stack row and lock together | `angee-framework` step 6 | `tests/test_stack.py` | — | — |
| Deliberate in-repo owners (transitions, hierarchy, impl registries, hooks) not swapped on a whim | owner index rows link the recorded reasons | — | architect ruling (`angee-design` step 7) | — |

## Composition, permissions, data

| Rule | Instruction | Check | Review | Unwired |
|---|---|---|---|---|
| Composition belongs to the composer; generated output is never edited | `angee-models` step 8; `angee-runtime` | `angee check` drift; `test_base_layering.py` (serving never imports the composer); the generated sentinel on clean | — | — |
| Serving never imports build owners | `angee-framework` step 2 | `test_layering.py::test_addon_serving_modules_never_import_build_owners`; `test_stable_serving_entrypoints_do_not_import_compose` | — | — |
| Authorization is REBAC-native and additive | `angee-permissions` steps 1–6 | `angee.E015`–`E017`, `E022`, `E024`, `E027`, `E045`; `rebac sync --check` | `django-reviewer` | — |
| Live schema and database are not damaged | `angee-permissions` step 7 owns the sync decision (ordinary, retirement, deliberate overwrite); `angee-runtime` delegates to it and orders build, migrate, sync, resources | `migrate --check`; the drop guard; provision's drift refusal | operator assignment reads job logs | backup before a destructive migration is an instruction only |
| Seed data is declared, not scripted | `angee-resources` | catalogue tier checks; `resources load` on a scratch database | — | — |
| Pages compose standard views; forms follow the save contracts | `angee-pages`, `angee-forms` | `expectValidBaseAddon`; `containersFromChildren` tests; vitest | `react-reviewer`; browser acceptance | — |

## Naming, vocabulary, process

| Rule | Instruction | Check | Review | Unwired |
|---|---|---|---|---|
| One name per concept; no customer or product vocabulary in the framework | `angee-design` step 3 collision sweep; `angee-addon` and `angee-models` *Do not* | — | `architecture-reviewer` | no static check for customer names |
| Thin callers; debt removed at its owner | the owner map template records the thin-caller and debt checks the gate requires | — | `architecture-reviewer`, `django-reviewer` | — |
| Stop and ask when a shape is still ambiguous | `angee-design` step 7 | — | the architect | — |
| Tests are authored with the artifact | `angee-tests`; each artifact skill points at it | the checks per area | — | — |
| Skills stay honest | every skill cites definition lines and names sources; transition notes only in `<details>`; no private history in bodies | `test_agent_methodology.py` (YAML frontmatter, skill-internal links only as `references/<file>`, `openai.yaml` policy true only for the guard, the `.claude/skills` link); `test_owner_index.py`; `test_agent_owner_map_guard.py` | the prose properties (owner rules cite a rule with its condition, no absolutes, no private history, no time-sensitive wording) are review obligations on every skill change | — |

## End-to-end route for a complete addon

`angee-design` → `angee-addon` → `angee-models` and `angee-permissions` →
`angee-graphql` and `angee-resources` → `angee-forms` and `angee-pages`,
with `angee-tests` beside each artifact → `angee-project` (host selection)
→ `angee-runtime` (build, migrate, provision) → acceptance in the browser.
A framework seam change starts at `angee-framework` instead of
`angee-addon`.

## Unwired, in one place

- Semantic ownership and duplication under a new name: review obligations,
  never a check. The owner index improves discovery; it proves nothing.
- Role boundaries are instructions plus review: `tools/sync-adapters.py`
  emits no `tools:` allowlist and Codex metadata carries no tool restriction
  ([README](README.md#harness-permissions)).
- The guard is advisory: the hook reminds once per session and never
  blocks; invocation of `angee-design` depends on the harness honouring the
  description.
- No static check for customer names, triggers or `RunSQL` in migrations,
  or a backup before a destructive migration.
