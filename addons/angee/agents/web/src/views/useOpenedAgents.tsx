// The keep-alive substrate shared by the two agent switchers — the side-chatter
// (`AgentChatterPane`) and the full-page sessions view (`AgentSessionsPage`) — so the two
// can never drift. It lives in the agents addon, not `@angee/ui`: the rendered tabs are
// `AgentChat`, which pulls in assistant-ui, and the @angee/ui chat layer stays
// assistant-ui-free.
//
// Keep-alive: each opened agent renders ONE stable `<AgentChat agentId={id} key={id}>`,
// and only the selected one is shown — the rest are hidden with the BARE `hidden` attribute
// (no display utility on the wrapper, so `[hidden]{display:none}` from preflight wins and
// removes the inactive instance from layout, the tab order, and the a11y tree, silencing its
// `role="status"` live region). Because `useAcpRuntime` keys its connect effect on `agentId`
// and resets the transcript only when its own `agentId` changes — which never happens for a
// stable keyed instance — each hidden instance keeps its own socket / ACP session / transcript,
// so switching back restores that agent's exact history with zero cross-wiring.
//
// Selection is owned by the CALLER (React state in the chatter, the URL `:id` on the sessions
// page); this owner only tracks which agents have been opened and bounds that set. LRU eviction
// unmounts a tab and drops only THAT tab's in-browser transcript (the server ACP session
// persists) — expected, not a leak. The documented fallback — a single `<AgentChat key={selectedId}>`
// rebind — is also leak-free but drops the prior in-browser transcript on every switch; keep-alive
// is chosen to avoid that.

import * as React from "react";

export interface UseOpenedAgentsOptions {
  /** The currently selected agent id (caller-owned); pushed into the opened set when first seen. */
  selectedId: string | null | undefined;
  /** LRU cap on simultaneously mounted agents — bounds one WebSocket + token-refresh timer each. */
  max?: number;
}

/**
 * Track the lazily-opened, LRU-capped set of agent ids. The selected id is appended on first
 * appearance and moved to newest on re-selection; when the set exceeds `max`, the least-recently
 * selected id is evicted (never the current selection). Returns the ids in least→most recent
 * order, stable across renders unless the membership/order actually changes.
 */
export function useOpenedAgents({
  selectedId,
  max = 5,
}: UseOpenedAgentsOptions): { openedIds: readonly string[] } {
  const [openedIds, setOpenedIds] = React.useState<readonly string[]>(() =>
    selectedId ? [selectedId] : [],
  );
  React.useEffect(() => {
    if (!selectedId) return;
    setOpenedIds((prev) => {
      // Already newest and within cap → keep the identity so we don't churn renders.
      if (prev[prev.length - 1] === selectedId && prev.length <= max) return prev;
      const next = [...prev.filter((id) => id !== selectedId), selectedId];
      return next.length > max ? next.slice(next.length - max) : next;
    });
  }, [selectedId, max]);
  return { openedIds };
}

export interface KeptAliveAgentsProps {
  /** The opened agent ids (from `useOpenedAgents`). */
  openedIds: readonly string[];
  /** The selected id — its tab is shown, the rest are hidden but kept alive. */
  selectedId: string | null | undefined;
  /** Render one agent's chat surface; called once per opened id with a stable key. */
  renderAgent: (id: string) => React.ReactNode;
}

/**
 * Render every opened agent's chat surface, showing only the selected one. Each tab is wrapped
 * in a `<div hidden={id !== selectedId}>` with the BARE `hidden` attribute and only height
 * utilities (no display/flex/block utility), so `[hidden]{display:none}` reliably removes the
 * inactive instance while keeping it mounted (socket + transcript preserved).
 */
export function KeptAliveAgents({
  openedIds,
  selectedId,
  renderAgent,
}: KeptAliveAgentsProps): React.ReactElement {
  return (
    <>
      {openedIds.map((id) => (
        <div key={id} hidden={id !== selectedId} className="h-full min-h-0">
          {renderAgent(id)}
        </div>
      ))}
    </>
  );
}
