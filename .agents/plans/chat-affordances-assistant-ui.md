# Chat affordances — lean on assistant-ui over ACP (executable plan)

Follow-on to `.agents/plans/agent-chat.md` (the original ACP chat build, Steps
0–4 DONE). Goal: **stop hand-building chat UX**. Wire `@assistant-ui/react`'s
built-in affordances — slash commands, attachments, copy, cancel, plan/mode —
onto the existing ACP `ExternalStore` runtime, sourcing each from ACP
`session/update` events we already receive but currently drop. Decision +
rejected alternatives: `.agents/notes/chat-ui-library-evaluation.md`.

Scope is `addons/angee/agents/web` + `packages/ui` (chat primitives). No backend
schema change expected (call it out if a phase needs one).

## Architecture gate

- **Owner map.** Chat-UX affordances (slash palette, action bar, attachment
  adapter, composer cancel, thread list) → `@assistant-ui/react` (locked owner,
  `docs/stack.md`). Agent contract (`available_commands_update`, `ContentBlock`,
  `plan`, `current_mode_update`, `cancel`, `promptCapabilities`) →
  `@zed-industries/agent-client-protocol` — all ride `session/update` /
  `initialize` we already handle. Styling/slots → `@angee/ui` (Base UI). The chat primitives are deliberately
  `@assistant-ui`-free (`packages/ui/src/communication/chat/index.tsx:1-3`), so an
  `@angee/ui` addition stays **presentation-only** (a styled slot like
  `ChatBubbleActions`); the assistant-ui *binding* (`ActionBarPrimitive`, the slash
  `TriggerPopover`, attachment controls) stays **inline in the addon**, styled with
  `@angee/ui` tokens — matching the existing pattern (`AgentChat.tsx:94-105`). Do
  not wrap an assistant-ui primitive inside `@angee/ui`. ACP→
  transcript folding → `acp-log.ts`; socket + store → `useAcpRuntime.ts`.
- **Sibling inventory.** One chat surface only (`addons/angee/agents/web`); the
  shared workshop is `packages/storybook/src/stories/Chat.stories.tsx` over the
  `@angee/ui` chat primitives. No second copy to reconcile. `Chatter.tsx` is the
  side/tab placement.
- **Dependency check.** **No new dependency.** Slash commands are `unstable_*`
  *within* `@assistant-ui/react` (installed **0.12.28**; all named APIs verified
  present in its type defs — see Phase 2 for the exact shapes); attachments /
  `ActionBarPrimitive` / `ComposerPrimitive.Cancel` are in-package. Manifests
  untouched.
- **Thin caller.** `AgentChat.tsx` stays a composition of primitives + runtime
  state; new logic lands in `useAcpRuntime` (store/dispatch) and `acp-log`
  (folding), not the view.
- **Deletion / altitude (honest).** This is feature-additive → lines increase.
  Justification: the owner (assistant-ui) already exists, so each affordance is
  ~10–40 lines of glue versus a bespoke component (hundreds of lines that drift).
  It preempts a hand-rolled slash/threads/attachment build — the real DRY win,
  though not a deletion this plan itself makes. (The `requestPermission`
  auto-approve stub at `useAcpRuntime.ts:281` and the `ToolPart` `args`-bag cast at
  `useAcpRuntime.ts:255` are separate follow-ups, not touched here.) No phase may
  fake a capability ACP lacks (see "Out of scope").
- **Naming.** One name per concept across runtime/view/story/tests
  (`availableCommands`, `attachments`, `plan`, `mode`) matching ACP's field names.

## Current state (verified, for grounding)

- `acp-log.ts` folds only `agent_message_chunk` / `agent_thought_chunk` /
  `tool_call` / `tool_call_update`; **drops** `available_commands_update`,
  `plan`, `current_mode_update`, `user_message_chunk` (→ `default` unchanged).
- `useAcpRuntime.ts` builds `useExternalStoreRuntime({ isRunning, messages,
  onNew, onCancel, convertMessage })`, single session per `agentId`. `onNew`
  sends a **text-only** prompt (`textOf`). `onCancel` already calls ACP `cancel`.
- `AgentChat.tsx` composes `@angee/ui` chat primitives + assistant-ui
  `Thread/Composer/Message` primitives. Composer shows **Send only** (no Stop).
  No slash palette, attachments, action bar, plan, or mode UI.

## Phases (cheapest → hardest; each ships independently)

### Phase 1 — Cancel + Copy (zero-risk, pure assistant-ui)
- Composer: show **Stop while running**. In `AgentChat.tsx`, gate `ComposerPrimitive.Send`
  on not-running and render `ComposerPrimitive.Cancel` when running (assistant-ui
  exposes the running state via `ThreadPrimitive.If`). `onCancel` already wired.
- Assistant message: add `ActionBarPrimitive.Root` + `ActionBarPrimitive.Copy`
  (copy assistant text). Compose into `ChatBubble` via an `@angee/ui` action-row
  slot — add `ChatBubbleActions` to `packages/ui/src/communication/chat/` if no
  slot exists.
- Verify: typecheck + a story state + click-to-copy / stop test.

### Phase 2 — Slash commands from ACP `available_commands_update`
- **Capture commands in runtime state.** Commands are not transcript parts — hold
  them in `useAcpRuntime` (`availableCommands: AvailableCommand[]`), updated from
  the client's `sessionUpdate` when `update.sessionUpdate === "available_commands_update"`
  (today that update is ignored). Expose on `AcpRuntime`. **Clear it inside
  `connect()`** (beside the `newSession` call) so BOTH reconnect paths reset it —
  the hard reconnect (effect re-run, `useAcpRuntime.ts:114-117`) and the silent
  token-refresh `connect(true)` (`useAcpRuntime.ts:178-185`), which does not re-run
  the effect and would otherwise keep stale commands.
- **Pick the interaction model — Directive, not the Action adapter.** ACP commands
  carry `input?: AvailableCommandInput` (`UnstructuredCommandInput` = *"all text
  typed after the command name is provided as input"*, `schema.d.ts:338,1605`), so
  the user must be able to type free-text args after choosing a command. That is
  `ComposerPrimitive.Unstable_TriggerPopover`'s **`Directive`** behavior (selecting
  inserts `/<name> ` into the composer; the user completes it and sends via the
  normal path) — **not** `unstable_useSlashCommandAdapter`, whose `execute()` fires
  immediately with no argument slot.
- **Render the palette.** Wire `<ComposerPrimitive.Unstable_TriggerPopover char="/">`
  into `ChatComposer`, listing `availableCommands` as `Unstable_TriggerPopoverItem`s
  whose `Directive` inserts `/<name> `. Render the items with the existing
  `@angee/ui` command/menu primitives (sibling: `CommandPalette`, `menu-parts`) — do
  **not** inline a menu. (For any no-arg subset that uses
  `unstable_useSlashCommandAdapter`, its real shape is
  `{ commands: [{ …, execute: () => void }] }` → returns
  `{ adapter, action, iconMap, fallbackIcon }`, spread onto the popover as `{...slash}`.)
- **Dispatch — verify against the agent first (entry gate).** ACP `PromptRequest`
  carries only `prompt: ContentBlock[]`; there is **no** command field or dispatch
  method (`available_commands_update` is the only command surface). So whether a
  `/<name> <args>`-prefixed prompt actually *invokes* the command is **agent
  behavior** (`claude-code-acp`), NOT protocol — `schema.d.ts` cannot confirm it.
  **Before building Phase 2**, verify against the running agent (via `angee dev`)
  that a `/`-prefixed prompt is invoked as a command; cite the agent as the owner of
  that fact. If it is not special-cased, the palette dispatches no-ops — a faked
  capability, which Mechanical Overrides forbids. Dispatch then reuses the existing
  `connection.prompt(...)` path; keep it in `useAcpRuntime`.
- **Isolate the `unstable_` API** behind one wrapper module so a future rename is
  a one-file change.
- Verify: a fake `available_commands_update` populates the palette; selecting a
  command sends the expected prompt (unit + story).

### Phase 3 — Attachments (paste / attach), gated on `promptCapabilities`
- Read the agent's `promptCapabilities` (from `initialize`/session) into runtime
  state; only offer image/audio/resource when advertised (baseline = text +
  `resource_link`).
- **Wire the attachment adapter explicitly.** Declare an attachment adapter
  (assistant-ui's `SimpleImageAttachmentAdapter`, or a custom one for files) and
  pass it as `adapters: { attachments }` on `useExternalStoreRuntime` (today the
  runtime call has no `adapters`). Paste/button then populate
  `AppendMessage.attachments`.
- **Map attachments → `ContentBlock[]` in `onNew`.** `onNew` reads text only today
  (`textOf`, `useAcpRuntime.ts:361-366`); extend it to read `message.attachments`
  and map each to an ACP `image` / `resource` / `resource_link` block alongside the
  text block.
- **Design fork (decide here):** inline small images as `image` blocks vs. route
  larger files through the **storage addon** (its owner) and send a `resource_link`.
  Cross-addon — storage owns files; do not re-upload from the chat addon.
- Verify: paste an image → an `image`/`resource` block is sent; the control is
  capability-gated off when the agent doesn't support it.

### Phase 4 — Plan + mode (latent ACP we already drop) — opt-in
- `plan` update → render the agent's task plan as a presentation-only `@angee/ui`
  plan/checklist slot. Note the semantics: a `plan` update carries the **full
  `entries` snapshot each time** (replace, not append), so hold it as **session
  state in `useAcpRuntime`** (replace-on-update), NOT folded through `acp-log.ts`,
  whose append/coalesce/upsert model fits poorly.
- `current_mode_update` + session modes → a mode switcher in `ChatHeader` (compose
  the existing `@angee/ui` menu), calling ACP's set-mode method. Capture the
  **initial** mode from `newSession().modes` (`SessionModeState`) into runtime state
  — today `newSession`'s result only feeds `selectSessionModel` and `session.modes`
  is dropped (`useAcpRuntime.ts:153-158`). Mirrors the existing `setSessionModel`
  model-selection path.
- Not in the original ask, but free affordances already on the wire.

### Phase 5 — SideChat + (optional) ThreadList — needs a scope decision
- **Clarify "SideChat":**
  - (a) **Placement** — a docked side-panel rendering of the *same* `AgentChat`
    (likely; `Chatter.tsx` is the tab container). Cheap: `AgentChat` is already
    `flex h-full`; add a narrow `SideChat` wrapper / responsive composition.
  - (b) **Multi-thread** — assistant-ui `ThreadListPrimitive` with each thread
    mapped to an ACP `newSession`. Requires a multi-session runtime (today it's
    one session per `agentId`). Bigger; defer unless wanted.
- Verify: docked render in storybook; (if (b)) thread create/switch.

## Out of scope (do not fake)
- **Edit / branch / regenerate.** ACP sessions are append-only streams — there is
  no protocol edit/regenerate. assistant-ui's `onEdit`/`onReload` would need
  "resend as a new prompt" semantics; decide deliberately later. Per the
  Mechanical-Overrides rule, do not paper a non-existent capability into the UI.
- **In-thread permission prompts.** The `requestPermission` auto-approve stub is
  intentional (server-side REBAC is the boundary). A real interactive prompt
  (assistant-ui can render `requestPermission` as a tool UI) is a separate slice.

## Verification (every phase)
- `pnpm --filter @angee/agents typecheck` + lint.
- Unit: extend `acp-log.test.ts` (new update kinds fold correctly / are ignored
  safely) and `useAcpRuntime.test.ts` (commands state, attachment content blocks,
  capability gating).
- Storybook: `Chat.stories.tsx` gains states — slash open, attachment chip,
  action bar, running/stop, plan block.
- Live: `angee dev` from repo root → a provisioned `RUNNING` agent → exercise
  slash + paste + copy + cancel against the real ACP stream.

## Risks
- `unstable_` slash API may churn across assistant-ui minors → isolate (Phase 2).
- assistant-ui 0.12 is fast-moving (~10.8k★, frequent releases) — pin and bump
  deliberately; a minor may relocate primitives.
- Phase 3 attachment transport (inline vs storage `resource_link`) is the real
  design decision — settle before coding.

## Future swap hedge
If TanStack AI's `@tanstack/ai-react-ui` reaches affordance parity (see the note),
the migration touches `useAcpRuntime`/`acp-log` (adapter) + the view, not the
`@angee/ui` primitives — keep that seam clean.
