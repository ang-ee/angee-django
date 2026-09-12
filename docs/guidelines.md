# Development Guidelines

Based on the [Apexive Development Philosophy](https://apexive.com/post/apexive-development-philosophy).
These guidelines own the development process and general coding principles.
[AGENTS.md](../AGENTS.md#constitution) owns the binding constitution; the language
guides apply it to their areas. Task size changes process, never engineering
standards: a small correction can be explained in a user update; structural work
needs an explicit owner map. A specialized workflow adds obligations only within
its stated scope and the user's authorization; it cannot relax the constitution.

## The Development Mantra

### 1. Research

Before writing code, understand the problem, its current owner, and nearby
solutions. Check [the stack](stack.md), search existing implementations and
relevant upstream patterns, then read the native manifest, model/manager/queryset,
shared primitive, compiler, or dependency API that should answer the question.
For structural work, record the [architecture gate](../AGENTS.md#architecture-gate)
before editing. Research should identify a concrete reuse/deletion path, not
become an unrelated survey.

Plans and notes recover history and intent; they are not current API references.
Verify their claims against code. Follow [knowledge routing](../AGENTS.md#where-knowledge-lives)
for persistent notes, including the conversation-only fallback when private
work-state is absent.

### 2. Think

Break the problem into its facts, owners, and constraints using
[first principles](#use-first-principles-thinking). Distinguish the needed
behavior from the implementation shape that first comes to mind.

### 3. Describe

State the objective and verification evidence in a concise working note or user
update. For architecture work, include the owner map, reuse evidence, dependency
choice, vocabulary, and expected deletion. Task-specific plans follow
the repository's knowledge-routing rules; a README carries durable project
intent, not a running task diary. Publishing an issue or message still requires
the user's authorization.

### 4. Discuss

Resolve uncertainty that affects requirements, architectural conventions, or
scope with the team or human architect. Existing patterns and explicit user
instructions should settle routine choices without another approval ritual.

### 5. Build

Implement through the established owners and the relevant
[backend](backend/guidelines.md) or [frontend](frontend/guidelines.md) rules.
Use the [checks guide](checks.md) for working directories, prerequisites, and
verification commands. Do not silently expand scope or weaken a contract to
make a failing check pass.

### 6. Stop

When the implementation grows through copies, boundary leaks, or ceremony, stop
extending that design. Reconsider the owner and the smaller native shape. Remove
encountered debt at its source. If removal requires a broader migration or exceeds
the user's authorization, describe the exact debt, owner-level fix, and blocker;
do not hide it with a workaround or claim the affected work is complete. See
[red flags](#avoid-red-flags).

### 7. Repeat

Use failures and review findings to refine the same objective. Update changed
contracts and their callers together, then verify the resulting behavior.
Report what ran and what remains unverified.

## Coding Principles

### Don't Repeat Yourself (DRY)

Every fact, rule, and reusable capability lives once at its owning level. Before
adding an implementation or extracting a helper, search the concept and nearby
names with `rg`, read the existing components and upstream APIs, identify the
owner, and apply these distinctions:

- Same rule twice: choose one owner and delete the copy.
- Repeated shape with shared intent: consolidate into the smallest useful owner.
- An existing owner already provides it: compose that owner, even for one caller.
- Generated duplication: fix the declaration or generator.
- Similar code with different intent: keep it separate and name the difference.
- Repeated prose: keep the durable rule with its owner and link to it.

Do not create a parallel implementation beside an existing owner or keep a
superseded path after migrating its callers. A DRY refactor must delete the copies
and make callers thinner; hiding copies behind another helper is unfinished work.
Every refactor must simplify ownership, callers, or maintained structure. Explain
any line growth by the missing behavior or owner it introduces and the
simplification it buys. Prefer deletion of wrappers, options, and dead paths to
another abstraction; measure total maintained code, not just the size of a caller.
A short summary pointing to an owner is useful; an independently maintained copy
of its exact contract is not.

### Keep Policy Above Detail

Policy lives on models, fields, managers, querysets, addon-owned use cases, and
shared framework primitives. UI, GraphQL resolvers, commands, filesystem emitters,
vendor SDK clients, and generated output translate inputs and dispatch to those
owners. They do not independently decide business state, persistence rules,
permissions, or cross-addon composition policy.

When policy needs an implementation detail, use the appropriate explicit
contract: an addon manifest, settings/autoconfig, `ImplClassField`, schema bucket,
slot, registered form/glyph, or addon-owned interface. Do not introduce a parallel
registry merely to avoid the framework's extension point.

### Put Behavior on the Owning Object

This applies [Find the owner](../AGENTS.md#constitution) at class scope:

- A record owns the fact: use a model method, property, or field behavior.
- A collection owns it: use its queryset or manager.
- An addon declaration owns it: use `addon.toml` and its native parsed manifest.
- Django app identity or lifecycle owns it: use `AppConfig` and native app hooks.
- No participant owns a cross-owner rule: use the smallest addon-local service
  or use case to orchestrate those owners.
- An entrypoint sees it first: parse/validate input, acquire context, dispatch,
  and format the result.

When a helper primarily interprets, mutates, or forwards to one object's
internals, move that behavior onto its owner. Dispatch that decodes another
owner's type or shape belongs behind that owner's interface; use its native
polymorphism or extension point. Keep functions loose for orchestration when no
participant owns the cross-object rule, pure transforms with no natural owner,
and thin integration entrypoints. For example, Django's `DateField.to_python`
owns field conversion and can call the ownerless `parse_date` string transform.

An entrypoint's budget is input validation, context acquisition, dispatch, and
result formatting. Business-state branching, query policy, permission decisions,
implementation selection, and model/component introspection belong at their
respective owners. Native adapters translate contracts; they do not duplicate
the policy on either side.

### Let Code Carry Code Contracts

Names, types, and docstrings explain current API shapes beside their owner.
Guidelines teach intent, invariants, and ownership; they should not repeat field
inventories, defaults, or model-specific algorithms. Link to the public owner and
its tests for exact behavior. A pitfall should state its trigger, the enduring
rule, and the owner to consult, not preserve an obsolete repair recipe.

### Reconcile Code, Docs, And Tests

Code shows current behavior; the constitution expresses intended invariants.
Neither a stale paragraph nor an accidental implementation establishes a new
architectural rule by itself. When evidence disagrees:

1. Read the implementing owner, its manifest/docstrings, focused tests, and the
   applicable invariant. Use history only to recover the reason for a change.
2. Identify whether the prose is stale, the implementation violates the intended
   contract, or the intended contract is genuinely undecided.
3. Correct stale prose to the verified owner, or fix defective behavior and add
   meaningful coverage. Do not weaken tests or rewrite policy to bless a defect.
4. If choosing a new convention is necessary, present the alternatives to the
   human architect. Reconcile affected callers, docs, and tests in the same change.

Historical migration guidance must name its applicable versions or state
conditions and the current owner. Once a migration is complete, remove superseded
instructions from active guides. An environment failure belongs in scoped
troubleshooting, not an unconditional framework rule.

### Use First-Principles Thinking

Separate the required facts and constraints from inherited implementation
assumptions, then compose the smallest design from proven primitives. Existing
framework patterns and locked dependencies are evidence to investigate, not
machinery to reconstruct from scratch.

### Follow Proven Best Practices And Patterns

Lean on native Django and React ownership and the dependency APIs listed in the
stack. Read the locked library's implementation, public types, and extension
points before declaring a gap. If upstream owns the concern, wire it directly;
do not rebuild it with a local helper, wrapper framework, or competing state.
Extend the shared Angee owner only for the missing composition behavior, then
make consumers reuse it. When a new convention or seam is necessary, add a
focused behavioral or architecture check that prevents the actual reinvention
or drift. Test outcomes and boundaries, not merely that prescribed words occur
in a file.

### Name So Code Can Be Found, Not Guessed

Names are the index of a framework:

- Use one name per concept across files, classes, methods, routes, settings,
  GraphQL, menus, tests, and docs.
- Encode roles consistently so a filename, class, and method agree about what
  owns the behavior.
- Follow the host framework's naming and discovery conventions. A new synonym
  is a design choice with a maintenance cost.

The [backend](backend/guidelines.md) and [frontend](frontend/guidelines.md) guides
own language-specific naming conventions.

### Avoid Red Flags

When a red flag appears, stop extending that implementation and reconsider its
owner and shape. Existing rot requires removal, not another layer that hides it.

#### The code is bigger instead of smarter

Repeated branches, copied shapes, and increasing ceremony reveal work to
simplify. Find the shared rule, reuse its owner, and delete the redundant code.
Required behavior can justify growth; duplication and speculative flexibility
cannot. Preserve clarity and contracts while reducing what must be maintained.

#### Spaghetti code

Tangled dependencies and mixed responsibilities make isolated changes difficult.
Keep policy with its owner and cross boundaries through explicit contracts.

#### You do not understand your own code

Trial-and-error patches that happen to pass tests need further research.
Explain the cause and resulting behavior before claiming the fix is understood.

#### Repeating coding work unnecessarily

Search before implementing. Reuse the native owner or repair its missing seam
instead of growing another partial version.

#### Boundary leaks and detail-driven policy

Permission decisions in React, model rules in resolvers, and view mechanics in
pages indicate that a caller has absorbed its owner's behavior. Move the rule
inward and keep the adapter thin.

#### Following antipatterns

Do not copy a familiar workaround without understanding its assumptions. Check
existing failures, current contracts, and native framework alternatives.

## Applying These Guidelines

Use the process above, the root [constitution](../AGENTS.md#constitution), the
relevant language guide, and [Checks](checks.md). Read the [glossary](glossary.md)
when a term is ambiguous and [the stack](stack.md) before changing dependencies.
