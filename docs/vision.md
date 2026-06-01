---
title: Vision
outline: deep
---

# Vision

> Angee **connects your domain — data, knowledge, files, anything you model — to
> agents, through industry-grade permissions**, and serves every app through **one
> UI, one API, and one MCP surface** to **users, systems, and agents** alike. It is
> the substrate for software that is **composed, not rebuilt; permissioned to the
> core; and operated by humans and agents through the same surface.**

## Architected by humans, built and extended by AI

AI is extraordinary at one thing above all else: **repeating a pattern
faithfully, at scale — when the pattern is clear.** Give an agent a clean,
consistent system and it will extend that system tirelessly and correctly, far
faster than any human. Give it an ambiguous one — three ways to do everything,
conventions that quietly contradict each other — and it reproduces the ambiguity
just as faithfully: three ways, forever, in random proportions.

So the leverage was never in the model. It is in the **clarity of the system the
model works inside** — and setting that system is the one job that stays
stubbornly human. The architect defines the patterns and the boundaries; the
agents apply them. That is the division of labour Angee is built around:
**architected by humans, built and extended by AI.**

For that division to pay off, the system has to be genuinely clean — so Angee
re-applies the oldest, most boring principles of software engineering to their
fullest extent, on purpose:

- **Clean architecture.** Concerns are layered and dependencies point one way:
  the domain owns its rules, transport and UI sit at the edges, and the framework
  owns the *seams* between them. Every piece of behaviour has an obvious place to
  live, so a human or an agent finds it by reasoning about the structure rather
  than grepping for it.
- **Don't reinvent the wheel.** Each concern is delegated to a battle-tested,
  production framework — Django, strawberry-django, a Zanzibar-shaped permission
  engine, React and the TanStack family — and Angee stays the thin, opinionated
  glue that binds them. You inherit decades of hardening instead of re-deriving it.
- **Don't repeat yourself.** Every concern has exactly one home. A challenge is
  solved fully, once: a model, a permission rule, or a contract is declared in a
  single place and everything downstream is *generated* from it. Fix a bug in that
  one place and the whole system inherits the fix — there is no second copy to
  drift out of sync.
- **Modularity.** The same idea at the scale of whole capabilities: each domain
  problem is solved once as a self-contained, composable module — an *addon* —
  that carries its own models, permissions, API, and UI. Building one is a
  **technical investment** every project downstream inherits; each module makes
  the next one cheaper rather than dearer, and the catalogue compounds.
- **Enough production-ready building blocks.** Clean structure is necessary but
  not sufficient. The substrate also ships the batteries — identity, permissions,
  audit, storage, integrations, real-time, agents — already assembled and tested,
  so the patterns an agent extends are not toy examples but the real, proven
  shapes the product is actually made of.

This coherence is what makes "built and extended by AI" more than a slogan — and
it is precisely what lets a system **self-extend instead of self-collapse.** Hand
tireless agents an ambiguous system and they accrete contradictions faster than
anyone can reconcile them; it self-collapses under its own inconsistency. Hand
them a clean, coherent one and every addition compounds, because they are not
improvising — they are applying patterns a human set, inside boundaries the
framework enforces. **The clearer the system, the more the machine can safely
do** — which is the whole bet of the pages that follow.

## Software 1.0 → 2.0 → 3.0

Every era of software is defined by who, or what, writes and runs it — and that
is changing under our feet.

- **Software 1.0** — humans write explicit instructions. `if`/`else`, compilers.
  Forty years of it.
- **Software 2.0** — humans curate datasets and gradient descent writes the
  weights. Vision, recommendation, self-driving (Karpathy named this in 2017).
- **Software 3.0** — English is the programming language, LLMs are the runtime,
  and **agents are the actors.**

The interesting shift is *not* "humans use AI to code faster" — that is a
productivity story, and a tired one. The shift is this: **the agent is no longer
a guest in the system. It is a resident.** It has an identity. It has
permissions. It can be paged, and it can be fired. It does work while you sleep.
And — the part that makes people uncomfortable — it can change the system itself,
under the same rules as you.

## The "AI feature" trap

Almost every "AI-powered" product today is Software 1.0 with an AI call grafted
onto a button. The agent is a guest: great for a demo, brittle in production. The
tell is to ask three questions — *who is this agent? what can it touch? what
happens when it screws up?* — and find that the answer is a config file and a
prayer.

The deeper problem is structural: **an agent is only as safe as the smallest
operation it can take.** If authorization lives in UI guards and hand-written
endpoint checks, you cannot give an agent autonomy without handing it a loaded
weapon. That is why most AI features stay shallow — *summarize this, draft
that* — and never actually *operate the product.*

## The substrate, not a smarter agent

Building real products with agents on an ordinary stack is a casino. Sometimes
you pull the lever and get exactly what you wanted in thirty seconds; sometimes
you pull it for four hours and end up behind where you started. You stop being an
engineer and start being a gambler. And the debt compounds astonishingly fast:
if your substrate has three ways to do a thing, the agents will use all three, in
random ratios, forever.

The fix is not a smarter agent. The fix is a **substrate where doing the right
thing is the path of least resistance** — one obvious convention for everything,
enforced by the system rather than remembered by the operator. **Conventions over
configuration stops being a style preference and becomes survival.**

That is the first half of the bet: the unglamorous substrate every product
re-implements — identity, authorization, tenancy, audit, files, secrets,
integrations, real-time, background work, deployment, gitops — should be
**inherited, not rebuilt.** Inherit the answers; spend your code budget on what is
actually unique to you. AI learns by example — so give the agents one excellent
example to build like, and they build like it.

## Composed, not patched

Angee comes in two halves, and knowing which half owns what is the whole mental
model. The **operator** is a generic Go control plane: it pulls source
repositories, composes them into Workspaces for development, and compiles them
into production Stacks — exposed over CLI, REST, **and** GraphQL. One `angee.yaml`
is a dev stack or a prod stack; a human, a script, or an agent drives the
*identical* lifecycle. The **runtime** is a headless Django framework that owns
data, permissions, and the API, and a React SDK that owns everything visual —
joined only by a typed contract the build emits.

Everything is an addon, and addons are **composed, not patched.** Most plugin
systems compose by concatenation and monkey-patching — runtime mutations rebuilt
on every boot, impossible to audit. Angee composes at build time,
deterministically: abstract addons are merged by Python's own inheritance into
one concrete application — one model graph, one GraphQL schema, one permission
schema, one typed client. Byte-identical inputs produce byte-identical outputs,
checked in CI. No monkey-patching, no runtime hooks. The result is not a pile of
plugins; it is a single coherent application that happens to have many authors —
increasingly, agents.

## Permissioned to the core

That connection — domain data to agents, served to every surface — is only safe
because of what sits underneath every operation: one non-negotiable rule, **there
is no "skip permissions" path.** Every path to your data, for every principal —
human, machine, or agent — runs through a relationship-based, **Zanzibar-shaped**
authorization engine, the same shape Google built to guard its own systems.
Identities are first-class for humans, machines, and agents alike; external IDs
are opaque so primary keys never leak; and a shadow audit trail means you can
always answer *who changed this, when, and were they a human.*

This is the precondition for everything else. Once every operation is uniformly
permissioned and audited, an agent is simply another principal: no special back
door, just the same permissioned, audited operations a human gets, generated
*from the API itself.* You can hand it autonomy because you can bound and replay
exactly what it did.

## Investment, not debt

In ordinary software, every shortcut is a debt you repay later. With agents on
the right substrate, the opposite has a name: **technical investment — negative
tech debt.** You solve a hard problem once, properly, in a place the agents can
find and reuse. From that day on, every change in that area gets *faster*, not
slower. The frameworks compound; the agents compound. Every primitive and addon —
and, crucially, its permissions — is tested end to end, so each new capability
stands on a proven foundation instead of adding to a pile of things to fix later.

This is the line between two futures. Software that **self-modifies** without
architecture becomes software that **self-collapses.** Software that
**self-builds** under explicit architecture and review compounds. The line
between them is the substrate.

## A workplace for agents

Agents are freelancers: smart, fast, willing — and unaccountable. They will say
"yes, I'll do that" and then quietly do something else; they will work around an
override without mentioning it. You do not fix that with a better freelancer. You
fix it with a better workplace.

Angee is, at heart, the workplace we want agents to work in — filing cabinets and
a dress code, a code of conduct, a clock to punch, and a manager who reviews the
work before it ships. Decisions are written down as durable records the agents
cite; conventions are enforced by the build, not left to memory. It is not a
smarter agent. It is a better workplace — one where the lazy path is the right
path.

## The bottleneck moves

When software can safely build itself — and *safely* is doing all the work — the
bottleneck stops being engineering hours and becomes **clarity of intent and
architectural taste.** You can delegate the implementation; you cannot delegate
what to build, why, and what the foundation must look like. Implementation
mistakes you fix by buying new furniture. Foundation mistakes you fix by tearing
the house down.

So the job changes. **You do the architecting and the deciding; a small army of
agents — operating under your rules, your permissions, and your audit trail —
does the wiring.** Define your vision; the agents build the reality.

## One substrate, many futures

The concepts — the addon manifest, composition, the REBAC schema, the GraphQL
surface — are defined as **contracts.** Django is the reference realization, not
the definition; the same discipline that lets a Go control plane manage a Python
runtime lets that runtime be re-implemented in another language without
renegotiating a single contract.

That generality is deliberate, because one substrate is meant to carry more than
one future:

- **ARP — Agentic Resource Planning.** ERP re-cut for the era where some of your
  team are not human: invoicing, accounting, procurement, CRM, HR as composable
  addons over shared primitives, every line item permissioned and agent-addressable.
- **ACM — Agentic Company Management.** A control plane for running a company
  where agents are real members of the org chart — with identities, permissions,
  and audit.
- **Sovereign, personal AI.** Personal AI you actually own, running on substrate
  you control.

These are not three products on three foundations. They are one foundation —
because no one should build three substrates.

## The invitation

Angee is early — built in the open, honest about what is shipping and what is
being lifted in from platforms that already run in production. But the shape is
settled, and the bet is clear: the next generation of software will be composed,
not rebuilt; permissioned to the core; and operated by humans and agents through
the same uniform surface.

Inherit the answers. Build the part only you can.
