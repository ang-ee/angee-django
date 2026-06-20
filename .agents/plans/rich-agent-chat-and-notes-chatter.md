# Rich agent chat UX + notes side-chatter — executable plan

Two deliverables, lifted in shape from the `angee-django-p1` prototype but
**reconstructed to our conventions** (lift, never copy):

1. **Full rich agent chat UX** — replace the raw `@assistant-ui` primitives in
   `AgentChat.tsx` with a proper chat surface: role bubbles, a status header with
   reconnect/clear, streamed markdown, **reasoning/thinking frames**, **rich
   tool-call cards (input + result)**, and a collapsible **system-context block**.
2. **Side-chatter → notes** — wire the right-rail `Chatter` "angee" tab in the
   notes example to the user's agent, bound to the open note, so the agent reads
   and edits notes (via the existing notes MCP tools) and sees the open record.

## What we keep (do NOT lift p1's plumbing)

p1 and our stack diverge below the UI; ours is the keeper in every case:

- **Transport/auth.** Ours mints a short-lived **per-actor route token** through the
  operator's central Caddy (`agentChatEndpoint` *mutation* → `{url, token, expiresAt,
  mcpServers}`) over `@zed-industries/agent-client-protocol`. p1 uses an in-container
  HMAC sidecar + a `agentAcpEndpoint` query. **Keep ours** — `useAcpRuntime` /
  `acp-transport.ts` stay as the transport; only the *rendering* changes.
- **MCP wiring.** Ours embeds the agent's `mcpServers` in the `agentChatEndpoint`
  result and authorizes every tool **server-side via rebac** (`rebac_mcp_tool` +
  `ANGEE_MCP_ACTOR_VERIFIER`). p1 has a separate `agentMcpServers` query and sudo-reads.
  **Keep ours** — no separate MCP query, no sudo.
- **Context.** `renderAgentPrompt(id, view)` + `context.py:render_view_context` are
  already model-generic (resolve `notes/note` via the rebac resource type) and
  secret-safe (EncryptedField excluded). **Reuse as-is** — no notes-specific resolver.

So the only new backend is a **primary-agent resolver** (Track 2); everything else is
frontend reconstruction.

## Decided dependencies

Already present in `addons/angee/agents/web/package.json`: `@assistant-ui/react`,
`streamdown`, `@zed-industries/agent-client-protocol`. **Add `use-stick-to-bottom`** for
auto-scroll (decision 4 — hand-roll as little as possible). The base chat primitives are
**presentational only** (no `@assistant-ui`/`streamdown` import) so `@angee/base` takes
**no new runtime dep** — the agents addon keeps the assistant-ui wiring. Reuse existing
`@angee/base` primitives wherever possible (`Popover` for the cog panel, `Kbd`, `Tag`,
`Button`, and the mono/scroll `CodeBlock` — add a minimal `CodeBlock` only if absent).

---

## Track 1 — Rich agent chat UX

### Step 1.1 — Presentational chat primitives in `@angee/base`

New module `packages/base/src/communication/chat/` (sibling to `Chatter.tsx`),
re-exported from `packages/base/src/communication/index.ts`. Pure styled components —
slots in, no data/assistant-ui coupling — using our token model (`tone`/`fill`,
`text-13`, `text-fg-muted`, `border-border-subtle`, `bg-sheet-2`) and `useBaseT` for any
copy. Mirror p1's `packages/base/src/communication/Chat/index.tsx` contracts:

| Component | Props | Renders |
|---|---|---|
| `ChatHeader` | `title`, `subtitle?`, `statusLabel?`, `statusTone?: Tone`, `actions?` | bordered header: status dot + title/subtitle (truncate) + actions row |
| `ChatHeaderAction` | `ButtonProps` (defaults size `sm`/fill `ghost`) | compact header button |
| `ChatBubble` | `role: "user"\|"assistant"\|"system"`, `children` | role-aligned bubble (user = end/brand, assistant = start/`bg-sheet-2` + border) |
| `ChatComposer` | `input`, `actions?`, `hint?` | composer frame (input slot + footer row) |
| `ChatComposerHint` | `children?` (default "⏎ send · ⇧⏎ newline") | `Kbd` + muted text |
| `ToolFallback` | `toolName`, `status?`, `input?`, `result?`, `isError?` | collapsible tool card: name + status tag, input + result in `CodeBlock` |
| `MessageReasoningFrame` | `children` | muted collapsible "Thinking" frame (`CodeBlock` tone muted) |
| `ContextBlock` | `label`, `children` | collapsible `<details>` with the system-context text in a scrollable `CodeBlock` |

Export `chatComposerInputClassName` (the shared textarea class) like p1. Add a
Storybook story per component (the repo's storybook-first convention) so they're
reviewable in isolation.

### Step 1.2 — Enrich the runtime (`useAcpRuntime.ts`)

Extend the message model and the `session/update` fold so the new components have data
(today thought chunks render as plain text and tool results are dropped):

- **Message shape.** Replace the flat `{text, toolCalls}` with ordered **parts** so
  reasoning, text, and tool calls interleave like p1:
  `type ChatPart = {kind:"text"; text} | {kind:"reasoning"; text} | {kind:"tool"; id; title; status; input?; result?; isError?}`.
  Keep the immutable fold (a new part/message object per update — the identity fix from
  the review still applies).
- **Fold rules** (`foldIntoLog`/`applyUpdate`):
  - `agent_message_chunk` (text) → append/coalesce into the trailing `text` part.
  - `agent_thought_chunk` → append/coalesce into a trailing `reasoning` part.
  - `tool_call` → push a `tool` part (`title`, `status`, `input` from `rawInput`).
  - `tool_call_update` → patch by `toolCallId`: `status`, and on `completed`/`failed`
    capture `result` (content) + `isError`.
- **Controls.** Expose `reconnect()` (tears down + `connect()`) and `clear()` (resets the
  log) on the returned `AcpRuntime`, plus the existing `status`. (p1's runtime exposes
  exactly these.) The silent-reconnect/`onUpdateRef`/log-reset fixes from the review stay.
- **Session info for the cog panel (decision 3).** Do *not* inject a system message.
  Instead surface the session's parameters for a settings popover: the resolved model, the
  MCP servers, the current view, and the rendered `<system_context>` for that view (render
  it on demand via `renderAgentPrompt(view)` when the panel opens, or expose the last block
  sent). `AgentChat` renders these behind a cog icon (Step 1.3). Keep prefixing the context
  to the prompt text on each send as today.

### Step 1.3 — Rebuild `AgentChat.tsx` on the primitives

Compose the new base components around the assistant-ui `Thread`/`Composer`/`Message`
primitives (keep the runtime + `AssistantRuntimeProvider`):

- `ChatHeader` with the agent status label + a **cog** `ChatHeaderAction` opening a
  `Popover` (decision 3) listing the model, MCP servers, the current view, and the rendered
  `<system_context>` (in a `ContextBlock`); plus **Clear** / **Reconnect** actions.
- `ThreadPrimitive.Messages` → `UserMessage` (`ChatBubble role="user"`) and
  `AssistantMessage` (`ChatBubble role="assistant"` with `MessagePrimitive.GroupedParts`):
  reasoning → `MessageReasoningFrame`, text → `Streamdown` (add `parseIncompleteMarkdown`),
  tool → `ToolFallback`. `SystemMessage` → `ContextBlock`.
- `AgentChatComposer` = `ComposerPrimitive` inside `ChatComposer` with `ChatComposerHint`
  and a `Send` button; disabled until `status === "ready"` (auto-scroll viewport via
  `use-stick-to-bottom` if we add it, else the existing viewport).

### Step 1.4 — i18n

Add agent-chat keys to `addons/angee/agents/web/src/i18n.ts` (`useAgentsT`) and any
generic chat copy to `@angee/base` `enBaseMessages` (`useBaseT`): status labels,
`chat.clear`, `chat.reconnect`, `chat.empty`, `chat.reasoning`, `chat.tool.*`,
`chat.context.label`. No hard-coded English in the components.

### Track 1 verification

`pnpm --filter @angee/base run typecheck` + storybook builds; `pnpm --filter @angee/agents
run typecheck`. Live: open a RUNNING agent's Chat tab, send a prompt → streamed markdown
renders incrementally and the viewport sticks to bottom, a tool call shows the rich card
with input+result, a thinking chunk shows the reasoning frame, the cog popover lists the
model + MCP servers + the rendered `<system_context>`, and Clear/Reconnect work.

---

## Track 2 — Side-chatter wired to notes

### Step 2.1 — `resolveSessionForView` (backend, the only new backend) — decision 2

The chatter knows the *view*, not the agent. One view-driven mutation resolves the agent
**and** mints the session in a single call (subsumes a `currentUserPrimaryAgent` +
`agentChatEndpoint` pair). Add to the agents schema (`addons/angee/agents/schema.py`):

- `resolve_session_for_view(info, view: JSON) -> AgentSession | None`. A **mutation** (it
  mints a route token = side effect). Returns the full session:
  `{agent: {id, name, status, model_handle}, url, token, expires_at, mcp_servers}`.
- **Agent selection (v1):** the **actor's** RUNNING, service-backed non-template agent
  (`Agent.objects.filter(owner=actor_user, is_template=False)`, RUNNING-first). `None` when
  the user has no running agent → the chatter shows a CTA. **Routing seam:** the body
  dispatches on `view["type"]`, so a later slice can route a `notes/note` view to a
  notes-specialized agent without changing the contract.
- **Endpoint minting:** factor the existing `agentChatEndpoint` body into a shared helper
  (`_mint_session(agent, actor)` → url/token/expiresAt/mcpServers) that both
  `agentChatEndpoint(id)` (agent-detail tab, knows the agent) and `resolveSessionForView`
  (chatter, knows the view) call — DRY, no duplicated token logic.

Reuse `renderAgentPrompt(id, view)` unchanged. SDL: `angee build` → `schema` → `schema
--check`; add a resolver test in `tests/test_agents_graphql.py` (owner-scoped,
RUNNING-preferred, returns a token + mcpServers, `None` when no running agent).

### Step 2.2 — `AgentChatterPane` + runtime generalization (agents addon web)

New `addons/angee/agents/web/src/views/AgentChatterPane.tsx`: the chatter-bound entry
(mirrors p1's `AgentChatter`). Props: the current view `{model, recordId}` from the chatter
host. It builds the envelope `{kind:"record", type: model, sqid: recordId}`,
calls `resolveSessionForView(view)`, and renders the Track-1 `AgentChat` against the
returned session — or an empty/CTA state (`useAgentsT`) when the mutation returns `None`.

**Runtime generalization.** `useAcpRuntime` currently mints via `agentChatEndpoint(id)`.
Generalize it to mint via a caller-provided **session source** so token-refresh re-mints
the right way: the agent-detail tab passes the `agentChatEndpoint(id)` minter (unchanged
`AgentChat` props — no collision with the parallel `recordTabs` work), the chatter passes
the `resolveSessionForView(view)` minter. Both return the same session shape, so the
runtime's reconnect/refresh is source-agnostic. Export the pane from the agents web index.

### Step 2.3 — Mount it in the notes example

`examples/notes-angee/addons/example/notes/web/src/NotePage.tsx` already registers the
`Chatter` tabs via `useChatterContent`; the **"angee" tab is an `EmptyState` placeholder**
— replace its `children` with `<AgentChatterPane model="notes/note" recordId={activeNoteId} />`.
That's the whole wiring point (no Chatter-shell changes). The agent then sees the open
note (context) and can read/list/create/update notes via the existing MCP tools.

- **Generalization (optional):** instead of per-page wiring, add an addon-level "chatter
  contribution" seam so any app gets the agent tab automatically (p1's
  `defineAddon({chatter})` model). Our current system is page-level (`useChatterContent`);
  a framework seam is a separate, larger change — keep page-level for this deliverable.

### Step 2.4 — Confirm the notes loop (no new code)

`renderAgentPrompt` already previews the open `notes/note` (capped, secret-safe), and the
notes MCP tools (`list_notes`/`read_note`/`create_note`/`update_note`) are rebac-gated and
already provisioned into the demo agent. Confirm the demo agent's `mcpServers` includes the
notes server and the route-token path reaches it.

### Track 2 verification

`schema --check`, `pnpm --filter @angee/agents typecheck`. Live (the real proof): `angee
dev`, open a note, open the chatter "angee" tab → it resolves the demo agent → ask "what's
in this note?" → the agent reads it (MCP `read_note` / the context block) and answers; ask
it to "add a line" → `update_note` writes and the note reloads. Watch the agent container
log for the `session/prompt` → tool-call round trip.

---

## Sequencing & checkpoints

```
1.1 base chat primitives ──┐
1.2 runtime enrich ────────┼─► 1.3 AgentChat rebuild ─► 1.4 i18n ─► Track-1 verify
                           │
2.1 primary-agent resolver ─► 2.2 AgentChatterPane ─► 2.3 NotePage mount ─► 2.4 confirm ─► Track-2 verify
```

Track 1 and Track 2.1 are independent and can start in parallel. Track 2.2 depends on the
Track-1 `AgentChat` surface and the 2.1 resolver. Land Track 1 first so the chatter mounts
the finished surface. Checks at each step: `ruff`/`mypy`/`pytest` (backend), `pnpm
typecheck` + storybook (web), `schema --check`.

## Coordination

A parallel agent is integrating `RecordTabDescriptor`/`recordTabs` in `FormView.tsx` +
`AgentsPage.tsx` (the agent-detail **Chat tab**). Track 1 makes that tab rich for free
(same `AgentChat`). **Do not edit `FormView.tsx`/`AgentsPage.tsx`** under this plan —
Track 2 touches only `NotePage.tsx`, the agents web `AgentChatterPane`/`useAcpRuntime`/
`AgentChat`, the base `chat/` module, and `agents/schema.py`.

## Decisions (resolved)

1. **Chat primitives live in `@angee/base`** — reusable (comments/activity later), matches
   "framework owns primitives".
2. **`resolveSessionForView(view)`** is the resolver — view-driven, returns the full
   session (agent + endpoint + token + mcpServers), with a `view.type` routing seam. No
   static primary-agent FK for v1.
3. **System context shows behind a cog icon** in the chat header (next to the model + other
   session parameters), not as an inline thread message.
4. **Use `use-stick-to-bottom`** for auto-scroll and lean on existing `@angee/base`
   primitives (`Popover`, `Kbd`, `Tag`, `CodeBlock`) — hand-roll as little as possible.

## Out of scope (future)

Message edit/regenerate/branch, attachments/images, an in-thread permission prompt UI
(today auto-approves; the server rebac scope is the boundary), the addon-level chatter
contribution seam, and a multi-agent switcher in the chatter.
