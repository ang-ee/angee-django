# Development Guidelines

> Based on the [Apexive Development Philosophy](https://apexive.com/post/apexive-development-philosophy).

Technology moves fast and only keeps accelerating. Traditional development cycles
are too lengthy and inefficient to keep up. These guidelines capture how we deliver
quality software fast — doing more with less, without compromising on quality.

The philosophy has two parts:

1. **The Development Mantra** — the process to follow for any piece of work.
2. **The Coding Principles** — the standards the resulting code must meet.

---

## The Development Mantra

Follow these steps, in order, for every task. Think before you build.

### 1. Research

Before writing a single line of code, research the problem and think of all
possible solutions. Don't rush into coding without a clear understanding of the
problem.

Ask yourself:

- **Have we done this before?** Check previous projects or modules. Ask the team.
- **Has anyone else done it before?** Look at open-source projects and modules.
- **What are the existing best practices?**

For Angee work, research is a small architecture inventory, not a broad essay:

- Check `docs/stack.md` for the dependency that owns the concern.
- Search the repo for the same concept, shape, page type, model pattern, schema
  pattern, or integration pattern before adding another one.
- Read the owner: the model/manager/queryset, `AppConfig`, shared primitive,
  schema builder, or dependency API that should answer the question.
- For structural refactors, record the owner map and expected deletion/reuse path
  in `.work/` or in the handoff note.

Specs, plans, and notes — in `.work/` or anywhere else — are snapshots of intent
at the moment they were written, and nothing reconciles them when the code moves.
Use them only to recover intent, history, and decisions; never as a reference for
how the system works now. The code is the only current reference: verify every
claim a spec or plan makes against the code before acting on it.

### 2. Think

Take time to think about the problem and possible solutions. Break down complex
problems into their basic elements using [first-principles thinking](#use-first-principles-thinking).

### 3. Describe

Outline your objectives. A specified goal keeps you focused and ensures your code
meets requirements. Keep the description concise and clear for teammates. Document
it in the README or a GitHub issue so the team has access.
Include relevant links or references. For architecture work, include the owner
map, sibling inventory, dependency owner, naming vocabulary, and what existing
code should become unnecessary.

### 4. Discuss

If you're unsure about any aspect of your code or the problem you're solving,
discuss it with your team or a knowledgeable colleague. Collaboration leads to
better solutions.

### 5. Build

Once you clearly understand the problem and the solution you want to implement,
start coding. Follow the best practices and established principles for the
framework you're using — for this project, that means the language-specific rules
in [Backend Guidelines](backend/guidelines.md) and
[Frontend Guidelines](/react/guidelines), and the library ownership in
[the opinionated stack](stack.md).

### 6. Stop

If your code becomes overly complex or difficult to read, stop. Consider a
different approach to [avoid red flags](#avoid-red-flags). It's okay to refactor
or rewrite code to improve its clarity and maintainability.

### 7. Repeat

If you hit a big roadblock, restart the process. Continuously improve your code
with feedback. Refine it until it meets quality standards.

---

## Coding Principles

### Don't Repeat Yourself (DRY)

Don't Repeat Yourself is a fundamental software development principle that
encourages avoiding code repetition. **Reuse highly tested existing code
whenever possible.**

Before copying code or extracting a helper, run the duplication gate:

- Search for the concept, name, shape, and nearby synonyms with `rg`.
- Identify the owner that should hold the fact or primitive.
- Same rule twice means choose one owner and delete the copy.
- Same shape three times means extract the smallest boring primitive.
- Generated duplication means fix the source declaration or generator.
- Similar code with different intent should remain separate and be named by that
  different intent.

DRY refactors should make callers thinner. If the line count grows, explain what
owner was missing and what deletion the new owner unlocks.

### Keep Policy Above Detail

Clean architecture is useful here only when translated into Angee's native
owners. Policy is the rule the product or framework cares about. Detail is the
transport, UI, storage, SDK, generated artifact, command, or resolver that lets a
user reach that rule.

- Domain policy lives on models, fields, managers, querysets, addon-owned
  services/use cases, and the shared primitives that own a framework behavior.
- Details include React components, GraphQL resolvers, management commands,
  filesystem emitters, vendor SDK clients, generated runtime code, and browser or
  CLI adapters.
- Details translate inputs, acquire context, call the owner, and format output.
  They do not decide permissions, persistence rules, business state, model shape,
  or cross-addon composition policy.
- Dependencies point inward toward the stable owner. If a policy needs a detail,
  invert through an explicit Angee contract such as settings/autoconfig,
  `ImplClassField`, schema buckets, slots, registered forms/glyphs, or an
  addon-owned interface.

### Put Behavior on the Owning Object

This is the class-scope face of **Find the owner** in
[`AGENTS.md`](../AGENTS.md#constitution).

Before changing code structure, make a quick owner map. Identify the owner of
each fact and place the behavior there:

- Persisted facts live beside the field, record, or schema item that stores them.
- Instance behavior lives on the instance type.
- Collection behavior lives on the collection abstraction.
- Declaration facts live on the declaring object.
- Commands, routes, and other entrypoints parse inputs and dispatch to the owner.
- Cross-object orchestration stays loose only when no single participant owns it.

Use this decision tree:

- One object owns the data: add a method, property, or field behavior there.
- A row owns it: use a model method/property.
- A row set owns it: use a queryset or manager.
- An addon declaration owns it: use the addon's `AppConfig`.
- A cross-owner workflow owns it: create the smallest addon-local service/use
  case and keep callers thin.
- A route, command, resolver, view, or event handler sees it first: parse,
  validate, acquire context, and dispatch; do not keep the rule there.

Rules belong beside the data they interpret. Prefer methods and properties on the
class that owns a fact over loose helper functions that repeatedly decode the
same shape from the outside. The test: if a function branches on — or repeatedly
reads — the internal shape of one object, it wants to be a method on that object;
a function that switches on a value's type is asking for polymorphism on that
type.

Keep a function loose for orchestration across objects, a pure transform with no
natural owner, or an integration entrypoint — and such a function may still call
into the owners. Django draws the line cleanly: `DateField.to_python` is a method
on the field, but it calls the ownerless `parse_date` to parse the string.

Do not fix hidden magic by adding ceremony. If a refactor replaces implicit
behavior with a larger explicit ritual, stop and look for the smaller native
framework shape.

Thin entrypoint budget: a command, route, resolver, React handler, resource
command, or worker task may parse/validate input, resolve actor/context, call the
owner/use case, and format the result. If it branches on business state, builds
query policy, duplicates permission logic, chooses a dependency implementation,
or inspects model/component shape, move that behavior inward.

### Let Code Carry Code Contracts

Guidelines teach principles and ownership rules; code owns current API details.
Do not keep lists of fields, classes, settings, or model-specific behavior in
prose when the contract can live in names, types, and docstrings beside the code.
Documentation may point to the owning module or class, but it should not become a
second spec that can drift.

### Use First-Principles Thinking

First-principles thinking is one of the best ways to reverse-engineer complicated
problems and unleash creative possibilities. Also known as "reasoning from first
principles," the idea is to break down complicated problems into their basic
elements and reassemble them from the ground up.

### Follow Proven Best Practices and Patterns

- Do not reinvent the wheel.
- Every piece of functionality should be built as a clean and reusable module.
- Let the host framework's conventional objects own the design. In Django, prefer
  reusable apps, `AppConfig`, models, fields, managers, querysets, forms, admin,
  management commands, settings, and the app registry over neutral helper modules
  or parallel registries. In React, prefer component composition, props, render
  derivation, route/search owners, form owners, and shared view primitives over
  mirrored local state or forked component trees.
- When a new convention or seam is necessary, add a check or focused test that
  prevents future reinvention and drift.
- Follow the best practices for the framework you're using — for example, PEP 8
  for Python. For this project, the specifics live in
  [Backend Guidelines](backend/guidelines.md) and
  [Frontend Guidelines](/react/guidelines).

### Name So Code Can Be Found, Not Guessed

This is a framework, and its names are the index people navigate by. Consistent,
predictable naming of files, folders, packages, classes, and methods lets a reader
know what a thing is called and where it lives without searching. Inconsistent
naming taxes every future reader, forever — so naming gets special attention here.

- **One concept, one name, everywhere** — across files, directories, packages,
  classes, and methods. A new name is a design decision; don't coin a synonym for
  something that already has one.
- **Encode the role in the name, consistently** — the file says what kind of code
  it holds, the class suffix says what kind of thing it is, the method verb says
  what it does, and they all agree.
- **Follow the host framework's conventions exactly** instead of inventing your
  own; match the surrounding ecosystem so the framework can locate code by name
  (convention over configuration). The concrete per-language conventions — modeled
  on Django for the backend — live in [Backend Guidelines](backend/guidelines.md)
  and [Frontend Guidelines](/react/guidelines).

> A **smart** person learns from their mistakes, but a truly **wise** person
> learns from the mistakes of others.

### Avoid Red Flags

Red flags are warning signs that your approach is going wrong. They rarely announce
themselves loudly — they creep in. Train yourself to notice them early, because the
cost of fixing them grows the longer they live in the codebase. When you spot one,
stop, step back, and reconsider the approach (see [Stop](#6-stop)) rather than
pushing through.

#### The code is bigger instead of smarter

If a feature keeps growing in size as you work on it, that's a signal the solution
isn't [DRY](#dont-repeat-yourself-dry) — you're solving the problem by adding more
code instead of finding the smarter, smaller abstraction.

- **What it looks like:** large blocks of near-identical code, sprawling functions,
  copy-pasted variations that differ by only a value or two, line counts that climb
  with every edge case.
- **Why it's bad:** more code means more surface area for bugs, more to read, more
  to test, and more to keep in sync. Volume is not progress.
- **What to do:** look for the underlying pattern and extract it into a single,
  well-named, reusable piece. Prefer a small, sharp abstraction over many concrete
  repetitions.

#### Spaghetti code

Tangled control flow and hidden dependencies where everything reaches into
everything else.

- **What it looks like:** deeply nested conditionals, functions that do many
  unrelated things, state mutated from far-away places, no clear boundaries between
  components.
- **Why it's bad:** you can't change one thing without breaking another, and you
  can't reason about a piece in isolation. It resists testing and onboarding.
- **What to do:** separate concerns into clean modules with clear inputs and
  outputs. Each unit should do one thing and expose a small, predictable interface.

#### You do not understand your own code

If you can't explain — clearly and simply — what your code does and why, that's a
red flag, not a detail to sort out later.

- **What it looks like:** code that "works" but you're not sure how; logic you'd
  struggle to walk a teammate through; changes made by trial and error until tests
  pass.
- **Why it's bad:** code you don't understand, you can't safely maintain, debug, or
  extend — and neither can anyone else. It's a liability disguised as a feature.
- **What to do:** simplify until it's clear. Rename things to say what they mean,
  break complex steps into named pieces, and remove cleverness that doesn't earn its
  keep. If you can't make it understandable, go back to [Think](#2-think).

#### Repeating coding work unnecessarily

Catching yourself solving a problem that has already been solved — by you, by the
team, or by the wider community — when a highly tested solution already exists.

- **What it looks like:** hand-rolling something a standard library, framework
  feature, or well-known package already provides; rewriting a utility that lives
  elsewhere in the codebase.
- **Why it's bad:** you're reinventing the wheel, and your version is almost
  certainly less tested, less robust, and more work to maintain than the established
  one.
- **What to do:** do your [Research](#1-research) first. Reuse existing, well-tested
  code. Don't reinvent the wheel.

#### Boundary leaks and detail-driven policy

Letting an outer detail decide an inner rule, or crossing a boundary by probing a
foreign object shape instead of using a declared contract.

- **What it looks like:** React deciding permissions, GraphQL resolvers
  re-deriving model rules, vendor SDK clients shaping domain models, serving code
  importing the composer, addon code importing downstream consumers, or page code
  recreating table/filter/form behavior.
- **Why it's bad:** the same policy gets rewritten in every adapter, and the next
  feature has to rediscover which copy is authoritative.
- **What to do:** move the rule to the owner and cross the boundary through the
  declared Angee contract: model fields/methods, managers/querysets, `AppConfig`,
  schema buckets, generated SDL, settings/autoconfig, slots, registered forms, or
  shared primitives.

#### Following antipatterns

Reaching for a "solution" that is a known mistake — a pattern that looks helpful but
reliably causes problems down the line.

- **What it looks like:** copying an approach without understanding it, choosing the
  expedient hack over the right design, or repeating a structure that has burned the
  team before.
- **Why it's bad:** antipatterns trade short-term convenience for long-term pain;
  they're traps that have already been documented as traps.
- **What to do:** learn the established best practices and patterns for your stack
  and follow them. Remember: a **smart** person learns from their own mistakes, but
  a truly **wise** person learns from the mistakes of others.

---

## Applying These Guidelines

This document is the shared **development process and coding principles** for all
work in the Django / React Runtime. It sits above the language-specific rules:

- **Process and principles (this file)** — how to approach any task, regardless of
  language or layer.
- **[Backend Guidelines](backend/guidelines.md)** — Python, Django, and the
  composer.
- **[Frontend Guidelines](/react/guidelines)** — TypeScript, React, and the
  rendered experience.
- **[The Opinionated Stack](stack.md)** — which library owns which concern.
- **[Glossary](glossary.md)** — shared vocabulary (composer, host, addon, seams…).
- **[AGENTS.md](../AGENTS.md)** — root rules and how the framework composes.

Follow this process first, then apply the relevant language-specific guidelines
during the [Build](#5-build) step.
