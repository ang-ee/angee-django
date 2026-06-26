# Chat UI Library Evaluation — CopilotKit vs assistant-ui vs TanStack AI vs Refine

Date: 2026-06-26

Scope: should we adopt an external framework for the agent **Chat / SideChat**
React surface (slash commands, copy/paste, attachments, streaming, tool
rendering) instead of maintaining our own — and does it touch our **ACP**
transport or our inference backend? Triggered by a look at CopilotKit.

Owners in play (see `docs/stack.md`):

- Chat-UX surface → `@assistant-ui/react` (locked).
- Agent transport → `@zed-industries/agent-client-protocol` (Zed **ACP**, locked).
- Headless primitives / styling → `@base-ui/react` + Tailwind 4 + `@angee/ui`.
- Data/state → Refine; inference → Django `InferenceBackend` (`anthropic`/`openai`).

## Executive Read

**Keep the current split. Do not adopt CopilotKit. Stay on `@assistant-ui/react`
and lean on its built-in affordances rather than hand-rolling them.** Watch
TanStack AI for a future consolidation once its UI matures.

Two facts decide it:

1. **"Chat" and "ACP" are different concerns.** Our ACP is the Zed *Agent Client
   Protocol* — the browser is a **client driving a running agent process**
   (`initialize → newSession → setSessionModel → prompt/cancel`, MCP, permissions)
   over WebSocket/ndjson through the operator's Caddy. No off-the-shelf React chat
   UI speaks ACP natively (browser ACP is "coming soon" upstream), so the
   **ACP↔runtime adapter is irreducibly ours** for *every* option — and we already
   own it (`useAcpRuntime` + `acp-log`). The "wheel" we don't want to maintain is
   the **affordance/UX layer**, not the transport.

2. **`@assistant-ui/react` already owns that affordance layer** — headless,
   transport-agnostic (`useExternalStoreRuntime`), MIT, market-leading
   (~10.8k★) — and it is already in our stack. CopilotKit/TanStack AI would add a
   *parallel* stack at layers we have locked owners for.

## Library comparison (the part that matters)

| | assistant-ui (ours) | CopilotKit 1.61 | TanStack AI 0.37 (Beta) | Refine AI |
|---|---|---|---|---|
| Shape | headless chat runtime + primitives | full agentic **stack** (UI + GraphQL transport + **Node runtime** + providers) | headless client + adapters + **headless UI** (`ai-react-ui` 0.8.x) | hosted SaaS **app-builder** (ai.refine.dev) |
| Wire protocol | transport-agnostic (ExternalStore) | **AG-UI** (SSE) | **AG-UI** + custom | n/a |
| Speaks ACP? | via our adapter | no | no | no |
| Headless primitives | own (Radix-*style*, no hard Radix dep) | `@radix-ui` + `@headlessui` | **truly headless** (only markdown deps) | n/a |
| Slash palette / ThreadList / attachments / action bar | **all built-in** | yes | **none yet** (chat/input/message/reasoning/tool only) | n/a |
| Backend fit (Django) | n/a (frontend) | needs Node sidecar or DIY AG-UI | provider-direct / AG-UI | n/a |
| Embeddable library? | yes | yes | yes | **no** |
| License / activity | MIT / ~10.8k★ | MIT / ~35.5k★ | MIT / ~2.8k★ | proprietary SaaS |

**CopilotKit — rejected.** It is a vertically-integrated stack that wants to own
UI *and* a GraphQL transport (`urql`) *and* a Node.js `runtime` (`type-graphql` +
Vercel AI SDK + LangChain) *and* an LLM provider registry. Against our locked
owners that is a second stack at four layers (`@radix-ui`+`@headlessui` vs
`@base-ui`; `class-variance-authority` vs `tailwind-variants`; `rxjs` vs
react-query/Channels; `zod` everywhere vs pydantic; its Node runtime vs
Django/Strawberry). High duplication for capability we largely already have. Its
overlap with us is shallow and at the leaf tier only (`react-markdown`,
`streamdown`, `@tanstack/react-virtual`, `tailwind-merge`, `lucide-react`,
`urql`) — i.e. nothing it would *save* us.

**Refine AI — not a library.** `@refinedev/ai` does not exist. "Refine AI" is a
hosted web IDE that *generates* Refine admin apps. Nothing embeddable; irrelevant
to a Chat/ACP surface.

**`@headlessui/react` — rejected** (came up because CopilotKit's `react-ui` uses
it). `@base-ui/react` already owns the headless-primitive concern (29 import
sites in `packages/ui/src/ui/*`) and is strictly more complete (Headless UI lacks
tooltip/slider/scroll-area/toolbar/nav-menu/number-field/toggle-group we already
use). Adding it = a duplicate, lesser owner.

## Watch item: TanStack AI

The most **stack-aligned** future option — we are already all-in on TanStack
(Router/Table/Virtual/Query), it is MIT and *genuinely* headless (no Radix, no
vendor-cloud upsell), and its `@tanstack/ai-client` (state/streaming/tools/MCP +
custom connection adapters) + `@tanstack/ai-code-mode` + devtools are compelling.
**Blocker:** `@tanstack/ai-react-ui` (0.8.x) ships only chat/input/message/
reasoning(`thinking-part`)/`tool-approval`/`tool-result` — **no slash palette, no
ThreadList, no attachments, no action bar**. Adopting it today would mean
hand-building exactly what we're trying to avoid.

**Re-evaluate when** `@tanstack/ai-react-ui` reaches assistant-ui affordance
parity (slash + threads + attachments + action bar). Our adapter
(`useAcpRuntime`/`acp-log`) is deliberately runtime-agnostic, so a future swap is
localized to those files.

## Decision

- Owner of the chat-UX surface stays **`@assistant-ui/react`**; of transport,
  **ACP**; of styling, **`@angee/ui`** / Base UI. No new dependency.
- Stop hand-rolling affordances — wire assistant-ui's built-ins, fed by ACP
  session updates we currently drop. See the implementation plan:
  `.agents/plans/chat-affordances-assistant-ui.md`.
- Optional follow-up: add a one-line "rejected: CopilotKit / @headlessui;
  watch: TanStack AI → this note" pointer near the `@assistant-ui/react` row in
  `docs/stack.md` so the next person doesn't re-litigate it.
