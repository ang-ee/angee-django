// The pure ACP→session-state reducer: folds the latent `session/update` notifications that
// describe the live session itself — not the message transcript — into an immutable snapshot
// the chat runtime exposes outside the message log. Kept free of React and assistant-ui so
// the fold rules are unit-testable on their own (see `acp-session.test.ts`); `useAcpRuntime`
// owns the socket and holds this snapshot as state. `acp-log` owns the transcript; this owns
// foreground state and available commands.

import type { AvailableCommand, SessionInfo, SessionNotification } from "@agentclientprotocol/sdk";
import { SessionUpdate, StateUpdate, type UpdateSessionNotification } from "@agentclientprotocol/sdk/experimental/v2";

/** Session navigation consumes ACP facts and commands; the host Query cache owns the list. */
export interface AcpSessionNavigation {
  currentId: string | null;
  available: boolean;
  ready: boolean;
  items: readonly SessionInfo[];
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  select: (id: string, cwd: string) => void;
  create: () => void;
  refresh: () => void;
}

/** A snapshot of the live session's latent state, held outside the message transcript. */
export interface AcpSession {
  /** The agent's advertised slash commands, from `available_commands_update`. */
  availableCommands: AvailableCommand[];
  /** v2 foreground work; v1 work is bound to the prompt response instead. */
  state: "idle" | "running" | "requires_action";
}

/** The session state before the agent advertises anything. */
export const emptySession: AcpSession = { availableCommands: [], state: "idle" };

/**
 * Fold one session update into `session`, returning a NEW snapshot when it carries latent
 * session state, or the SAME reference otherwise (mirroring `foldIntoLog`'s no-op skip, so
 * the store bails on `Object.is` and does not re-render).
 */
export function foldIntoSession(session: AcpSession, note: SessionNotification | UpdateSessionNotification): AcpSession {
  const update = note.update;
  if (SessionUpdate.isAvailableCommandsUpdate(update)) {
    return { ...session, availableCommands: update.availableCommands.map(({ name, description }) => ({ name, description })) };
  }
  if (SessionUpdate.isStateUpdate(update)) {
    if (StateUpdate.isIdle(update)) return { ...session, state: "idle" };
    if (StateUpdate.isRunning(update)) return { ...session, state: "running" };
    if (StateUpdate.isRequiresAction(update)) return { ...session, state: "requires_action" };
  }
  return session;
}
