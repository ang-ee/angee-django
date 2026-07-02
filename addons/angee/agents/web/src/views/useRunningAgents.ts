import { useAuthoredQuery } from "@angee/refine";
// The one owner of "the user's running agents", shared by both agent switchers — the
// side-chatter (`AgentChatterPane`) and the full-page sessions view (`AgentSessionsPage`) —
// so the running-set query, its live dependency, and the running/template filter live once
// and the two can never drift.

import * as React from "react";

import { AgentRoster, type AgentRosterItem } from "../documents";

/**
 * A roster row is a *running* agent when its observed `runtime_status` is RUNNING and it is
 * not a template (a template never runs a session). `runtime_status` types as the UPPERCASE
 * `RuntimeStatus` enum union ('RUNNING' | 'STOPPED' | 'ERROR' | 'WARNING'), so this is an
 * exact-case compare against the wire value.
 */
export function isRunningAgent(agent: AgentRosterItem): boolean {
  return agent.runtime_status === "RUNNING" && agent.is_template !== true;
}

/**
 * The user's running agents (newest-activity-first) plus a first-load flag. Reads the full
 * `AgentRoster`, declaring the `agents.Agent` live dependency so the set refreshes on
 * provision/deprovision, then filters to the running, non-template agents client-side.
 * `loading` is true only on the very first fetch (before any data has arrived).
 */
export function useRunningAgents(): {
  running: readonly AgentRosterItem[];
  loading: boolean;
} {
  const { data, fetching } = useAuthoredQuery(AgentRoster, undefined, {
    models: ["agents.Agent"],
  });
  const running = React.useMemo(
    () => (data?.agents ?? []).filter(isRunningAgent),
    [data],
  );
  return { running, loading: fetching && !data };
}
