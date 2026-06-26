# A6 — Per-agent MCP tool authz: DEFERRED (documented known limitation)

> Status: **DEFERRED by architect decision.** Not scheduled for execution now. This file is the deferral record so the gap is not silently forgotten — it converts a latent security limitation into a documented, trip-wired one.

## The limitation

Per the plans-record audit, an autonomous agent that holds an MCP server's credential currently:
1. **borrows the full identity of its credential-owner** (the human user), rather than acting as a distinct agent subject; and
2. has **no per-tool authorization** — any holder of the server's credential can reach **every** registered tool.

So the interim authorization model is effectively *"agent = credential owner, full CRUD over all registered tools."* That is a genuine privilege-scope gap for autonomous agents, not cosmetic.

## Where it lives (code already self-documents it)

- `addons/angee/agents/mcp_verifier.py:50` — `TODO(agent-identity, option B — deferred)`: the agent borrows its credential-owner's identity; a future design creates a distinct `agents/agent` subject grantable independently while preserving agent identity through CRUD (requires resource-schema changes + per-agent grants).
- `addons/angee/agents/mcp_verifier.py:56` — `TODO(mcp-authz)`: no per-tool authz yet — any holder of the server credential reaches every tool.

These are the only two TODOs in `addons/angee/agents/*.py`.

## The planned fix (when un-deferred)

Build under **`mcp-over-graphql.md` B2** (`.agents/plans/mcp-over-graphql.md`) + `agent-chat.md` Step 2:
- A distinct `agents/agent` REBAC subject identity (so an agent is grantable independently of its owner).
- An `Agent.mcp_tools` M2M allow-list so a server credential only reaches the tools its agent is granted.
- Per-tool authorization in the verifier (`mcp_verifier.py`) keyed on the agent subject + the allow-list, rather than on the owning user's identity.

Effort: M. Risk: **High (security — the privilege scope of autonomous agents).**

## Interim acceptability + trip-wire

Acceptable to defer **only while** MCP servers are operated by trusted operators in trusted deployments (the credential is not handed to untrusted parties). The moment that assumption weakens — multi-tenant MCP, untrusted agent operators, or agents granted to non-owners — A6 must be un-deferred **before** that ships.

**Recommended durable surfacing (do this even while deferred):** add a one-line entry to the `Pitfalls` / security notes in `docs/backend/guidelines.md` naming this limitation and pointing at `mcp_verifier.py` + this file, so a future agent wiring MCP doesn't assume per-tool authz exists. (The code TODOs cover the implementer; the doc note covers the architect/reviewer.)

## Reconcile

When un-deferred and built, tick the A6 row in `harmonisation-cleanup-plan.md` and close `mcp-over-graphql.md` B2.
