---
name: angee-design
description: "Runs the Architecture Gate before adding any model, field, page, permission, job, helper or module in an Angee repository (framework, product or customer addon). Names the facts, finds each owner in the index, searches nearby addons and sibling repos, decides the level, writes the owner map."
---

# Angee Design

The [Architecture Gate](../../../AGENTS.md#architecture-gate) as a short
procedure: before the first source edit of any feature at any level, produce the
owner map with its deletion list in the current note. It mandates no reviewers;
reviewers run where the gate or the chosen workflow (`slice`) requires them.

## Use when

- "add a model / field / column / flag / status / parent / position to …"
- "add a page / tab / form / action / column / permission / role / share for …"
- "add a job / task / helper / service / util / registry / settings for …"
- "implement <capability> in <addon>" or "port <feature> from <other repo>"
- any plan whose steps name a new module, class or table

## Owners

| File or module | Owns | Reference |
|---|---|---|
| `AGENTS.md` | the gate's checks and the level definitions | [Architecture Gate](../../../AGENTS.md#architecture-gate), [Repository Role](../../../AGENTS.md#repository-role) |
| `references/owners.md` | search aliases to the owning symbol, rule and applicability | [owner index](references/owners.md) |
| `docs/stack.md` | which dependency or deliberate in-repo owner holds a concern | [Backend](../../../docs/stack.md#backend), the "library choice" paragraphs |
| `docs/glossary.md` | the one name per concept | [Glossary](../../../docs/glossary.md) |
| the current note | the owner map and deletion list | lane report under the work-state slot, else the conversation ([Where Knowledge Lives](../../../AGENTS.md#where-knowledge-lives)) |
| sibling slots | consumers of a changed contract | `../angee-arp`, `../angee-messaging-bridges` beside this checkout when the workspace materializes them |

## Reuse first

Read in this order and record what each answers: `references/owners.md`
(`grep -i <keyword>`, then the owner's docstring at the cited `path:line` and the
linked rule); the nearest two addons under `addons/angee/` carrying the same kind
of fact, plus `examples/addons/example/notes` as the consumer-level reference;
[docs/stack.md](../../../docs/stack.md#backend) for the library that owns the
concern, its native API before any helper; the sibling slots for the same
concern and for consumers of any contract the feature changes.

## Steps

1. **Name the facts.** List every fact the feature introduces, one per line, in the vocabulary the request used: data (model, column, edge, constraint), behaviour (verb, lifecycle, job), access (permission, role, visibility), surface (page, form, tab, action), composition (setting, manifest key, dependency), as `fact | kind | owner?` rows (`hidden after pay | flag | ?`).
   **Owner rule:** [DRY](../../../docs/guidelines.md#dont-repeat-yourself-dry) decides per line: the same fact under a second name is a copy to delete; similar facts with different intent stay separate and the line names the difference.

2. **Look each fact up.** For every line, `grep -i` the keywords in [references/owners.md](references/owners.md). On a hit, read the owner's docstring and the governing rule, then write the owner symbol in the third column and the applicability condition that admits it. If the condition does not hold (archive is not trash; user-named stages are not code-owned states; a row-selected impl is not a closed `TextChoices`), the hit is evidence for the neighbouring owner, not a licence for a new one.
   **Owner rule:** the index points; the linked rule decides ([owner index](references/owners.md)). When the docstring and the guide disagree, [Reconcile Code, Docs, And Tests](../../../docs/guidelines.md#reconcile-code-docs-and-tests) applies: read the implementing owner and its tests, and do not pick whichever is convenient.

3. **Search two nearby addons and the sibling repositories.** `rg -n "<concept>|<column>|<verb>" addons/angee/<a> addons/angee/<b> examples/addons` and the same in `../angee-arp ../angee-messaging-bridges` when present. Record what exists: the owner to compose, or the concrete gap (which native extension point of which owner does not cover this fact). Run the collision sweep on every new name: `rg -in "<word>|<word family>" addons docs packages` — a word with an existing meaning is dead.
   **Owner rule:** [DRY](../../../docs/guidelines.md#dont-repeat-yourself-dry) applies when an existing owner already provides the capability: compose it even for one caller and name any gap at that owner. [Put Behavior on the Owning Object](../../../docs/guidelines.md#put-behavior-on-the-owning-object) applies when no participant owns a cross-owner rule: the smallest addon-local service or use case orchestrates the owners, which is composition, not a workaround. Similar code with different intent stays separate and names the difference.

4. **Decide the level** per [Repository Role](../../../AGENTS.md#repository-role):
   - If the fact is a generic row contract any addon could compose (a flag, lock, edge, ordering, identity) → `angee.base`, extending the existing mixin or field before adding one.
   - If it is a reusable product capability shipped with Angee (money, tags, files, threads, jobs, workflows, provenance, decisions, knowledge, agents, integrate) → that framework addon; a cross-addon rule goes to the base or capability addon both compose.
   - If it is one product's logic → the consumer addon, contributing to framework definitions additively (`permissions.extends.zed`, `extends`, type and input extensions, containers).
   - If it is composition mechanics (discovery, ordering, emission, serving) → core, with no product vocabulary.
   **Owner rule:** [Repository Role](../../../AGENTS.md#repository-role) decides by the fact's reach: "every fact lives at its owning level", bounded on both sides — a fact the framework should own does not stop at the consumer, and product vocabulary never moves below the consumer addon.

5. **Check the dependency.** For a concern a library owns per [docs/stack.md](../../../docs/stack.md#backend), wire that library's native type and extension point. A new dependency needs its stack row and manifest entry together ([Change Policy](../../../docs/stack.md#change-policy)).
   **Owner rule:** the [Django-Native Rule](../../../docs/backend/guidelines.md#django-native-rule) ("A wrapper must prove it adds a real new concept") keeps a wrapper only when it proves a real new concept; one that only forwards, normalizes or renames the library object is deleted. The stack row decides which library owns the concern; [Change Policy](../../../docs/stack.md#change-policy) decides when a new one is admitted.

6. **Write the owner map and deletion list** into the current note: the lane report when the workspace has a work-state slot ([Resolve Work-State](../angee-workspace/SKILL.md#resolve-work-state)), the conversation otherwise.

   ```markdown
   ## Owner map — <feature>
   | fact | owner (symbol, module) | composes / extends | applicability quoted from |
   | hidden after pay | ArchiveMixin, base.models.trash | composes | `ArchiveMixin` docstring, addons/angee/base/models/trash.py |
   | line items | HasuraLines + RowLockMixin | composes | frontend:1527-1540 |
   Level: consumer addon `<name>` · Dependency: none new · Names: swept `<words>`
   Thin caller: <each dispatching entrypoint (resolver, command, route, page) → the owner holding the rule it calls>
   Debt: <rot met in the affected owners and callers and what removes it at its source, or the exact migration/authorization blocker>
   Deletion list: <copies, wrappers, options, obsolete paths this removes>
   Growth: <what grows, the missing behavior or owner that justifies it and the simplification it buys, or "none">
   Open: <shapes still ambiguous, each with two concrete snippets>
   ```

   **Owner rule:** [Let Code Carry Code Contracts](../../../docs/guidelines.md#let-code-carry-code-contracts) splits the two: the owner's docstring carries its contract and the applicability condition the map quotes; the note carries the map, which is history, not reference ([Where Knowledge Lives](../../../AGENTS.md#where-knowledge-lives)). Promote an enduring lesson into the owning guide; never restate the map in a docstring.

7. **If a shape is still ambiguous, stop and ask the architect** ([Discuss](../../../docs/guidelines.md#4-discuss)). Present each option as the engineer-facing snippet a consumer would write, with its owner and what it deletes; name the recurring pattern if the same shape appears in other models or repos, and ask for the generic mechanism instead of a local choice. Do not start source edits on an open question.
   **Owner rule:** the [Constitution](../../../AGENTS.md#constitution) sends a convention to the architect only when, after researching the owners, multiple plausible shapes remain; a shape the index or a linked rule already decides is not a question.

8. **Continue** with the artifact skill that owns the result where the repository ships one, else the owning guide section from the owner map; run `slice` when the user requested it. The route for a feature is `angee-addon` → `angee-models` and `angee-permissions` → `angee-graphql` and `angee-resources` → `angee-forms` and `angee-pages`, with `angee-tests` beside each step → `angee-project` → `angee-runtime` → acceptance; a seam change starts at `angee-framework`.



## Do not

- A second boolean (`is_deleted`, `hidden`, `active`) for removal or archival → `TrashMixin` / `ArchiveMixin`; [Persistence primitives](../../../docs/backend/guidelines.md#persistence-primitives) **Archive and trash are different facts**.
- A status string with `if` chains or a transition library → `StateField` + `StateTransitions`; [Stack: backend](../../../docs/stack.md#backend) "State-transition library choice".
- An integer `position` with renumbering or epsilon guessing → `FractionalRankField`; [Rules](../../../docs/backend/guidelines.md#rules) "Manually ordered rows use `FractionalRankField`".
- Walking `parent` in Python or a recursive CTE → `HierarchyMixin`; [Stack: backend](../../../docs/stack.md#backend) "Tree library choice".
- A dotted-path column, `import_string` from writable data, an entry point or a private registry dict → `ImplClassField` + its base's named settings mapping; [Stack: backend](../../../docs/stack.md#backend) "Implementation-registry choice".
- Reading `os.environ` or a private settings parser → `autoconfig.py` `SETTINGS`; [Autoconfig](../../../docs/composer.md#autoconfig).
- A permission decision in a resolver, in React or in a Zed caveat → the model's Zed plus `authorized_permission_target`; [Boundary leaks](../../../docs/guidelines.md#boundary-leaks-and-detail-driven-policy), `angee.E024` (`addons/angee/base/checks.py:31-37`).
- A hand-rolled page, table or form → `ListView`/`RowsListView`/`FormView` and `registerForm`; [Frontend rules](../../../docs/frontend/guidelines.md#rules) **Routes and pages stay thin**.

## Verify

Done means the current note holds the owner map with every fact resolved to an
owner or a named gap, the level, the dependency choice, the swept names and the
deletion list; open shapes are questions to the architect, not edits. The index
is validated by `tests/test_owner_index.py` (every Owner cell cites a symbol,
each cited line declares it, and every public foundation class and addon
manifest has a row) and the link contract test under
[Agent methodology and documentation](../../../docs/checks.md#agent-methodology-and-documentation),
both static and runnable in any lane. The feature's own checks belong to its artifact
skill and [Checks](../../../docs/checks.md#python-and-composition-contracts); `slice` owns the
build-review-verify loop. A green suite is not a passed gate: the gate fails on a map
that names a copy, a wrapper or an unowned fact.

## Pitfalls

- Name a module by the Django-native owner it extends (`models.py`, `managers.py`, `schema.py`), never by a domain noun: a noun module beside `models.py` takes verbs, generators and survivorship away from their owners; [Django-Native Rule](../../../docs/backend/guidelines.md#django-native-rule) "Django-native also means app-native".
- Before adding a mechanism (a reconciler, a marker inference, a parallel table), find the one explicit line in an existing owner file that carries the fact and extend it; [Keep Policy Above Detail](../../../docs/guidelines.md#keep-policy-above-detail) "When policy needs an implementation detail, use the appropriate explicit contract".
- Grep the guides for a mechanism before choosing it: a database trigger or function never maintains a row, a revision or a rule; [Rules](../../../docs/backend/guidelines.md#rules) **Never a database trigger or function**.
- A removed or renamed export is a consumer contract change, however unused it looks here: search the sibling slots and land companions in dependency order; [Consumer CI Publication](../../../docs/checks.md#consumer-ci-publication).
- Sweep a candidate name's word family across addons, docs and packages before debating its merit: a collision or a product word in a framework name is disqualifying; [Name So Code Can Be Found, Not Guessed](../../../docs/guidelines.md#name-so-code-can-be-found-not-guessed).
- Measure total maintained code against the base on every redesign: growth is admitted only for the missing behavior or owner it introduces and the simplification it buys, and the map names what grew; unexplained growth beside an existing owner is a parallel implementation; [DRY](../../../docs/guidelines.md#dont-repeat-yourself-dry) "Do not create a parallel implementation beside an existing owner", [Architecture Gate](../../../AGENTS.md#architecture-gate) deletion check.

## Read next

- [AGENTS.md — Architecture Gate](../../../AGENTS.md#architecture-gate), [Repository Role](../../../AGENTS.md#repository-role)
- [docs/guidelines.md — Put Behavior on the Owning Object](../../../docs/guidelines.md#put-behavior-on-the-owning-object), [Keep Policy Above Detail](../../../docs/guidelines.md#keep-policy-above-detail)
- [docs/backend/guidelines.md — Rules](../../../docs/backend/guidelines.md#rules), [docs/frontend/guidelines.md — Component ontology](../../../docs/frontend/guidelines.md#component-ontology)
- capability guides beside the engine a feature extends, loaded by path from a source checkout: [mcp-tools](../../../addons/angee/mcp/skills/mcp-tools/SKILL.md), [jobs-tasks](../../../addons/angee/jobs/skills/jobs-tasks/SKILL.md), [integrate-bridges](../../../addons/angee/integrate/skills/integrate-bridges/SKILL.md), [workflow-definitions](../../../addons/angee/workflows/skills/workflow-definitions/SKILL.md), [provenance-attribution](../../../addons/angee/provenance/skills/provenance-attribution/SKILL.md), [agent-composition](../../../addons/angee/agents/skills/agent-composition/SKILL.md); other engines keep only `addons/angee/<addon>/README.md`
- skills: `slice`, `angee-workspace`; command: `lift` (reconstructing from another repo)

## Sources

<!--
AGENTS.md, docs/guidelines.md, docs/glossary.md (whole); docs/stack.md #backend; docs/backend/guidelines.md #django-native-rule, #package-layering, #composition-owners, #rules, #rebac, #persistence-primitives, #migration-history, #addon-websocket-endpoints, #graphql-actor-and-write-contracts, #integration-and-worker-contracts, #workflow-contracts, #naming, #checks;
docs/frontend/guidelines.md #native-state-owners, #reserved-translation-namespace, #component-ontology, #rules, #addresses-kinds-and-inheritance, #authoring, #narrowing-and-when; docs/composer.md #addon-discovery, #autoconfig; docs/checks.md #choose-the-working-directory, #python-and-composition-contracts, #agent-methodology-and-documentation, #what-ci-actually-runs;
addons/angee/base/{models,fields}/*.py docstrings and the class docstrings cited in references/owners.md; base/implementations.py:1-60, expressions.py, checks.py:26-38; graphql/trash.py:50-70; core/angee/hooks.py;
money (README, autoconfig, fields); tags (models, zed, pyproject.toml, TagForm, index.tsx); READMEs of messaging, workflows, provenance, integrate, decisions, knowledge, storage; examples/addons/example/notes (README, models, mcp_tools);
tests/test_agent_methodology.py, tests/test_documentation_contracts.py; skills slice, angee-workspace; commands/lift.md;
Mismatches: addon-level skills ship only for mcp, jobs, integrate, workflows, provenance and agents (addons/angee/<addon>/skills/<name>/SKILL.md); other engines keep only their README; the glossary has section anchors only; docs/backend/guidelines.md#graphql-actor-and-write-contracts (**Lines are owned parts, written as the user**) links `Stage` to addons/angee/work/models.py while the abstract owner is addons/angee/base/models/stages.py:37.
-->
