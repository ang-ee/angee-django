// The pure ACP→session-state reducer: folds the latent `session/update` notifications that
// describe the live session itself — not the message transcript — into an immutable snapshot
// the chat runtime exposes outside the message log. Kept free of React and assistant-ui so
// the fold rules are unit-testable on their own (see `acp-session.test.ts`); `useAcpRuntime`
// owns the socket and holds this snapshot as state. `acp-log` owns the transcript; this owns
// everything else the agent advertises about the session. The slash-command list lands here
// now; the agent's task `plan` and `current_mode` arrive here in a later phase.

import type { AvailableCommand, SessionNotification } from "@agentclientprotocol/sdk";

/** A snapshot of the live session's latent state, held outside the message transcript. */
export interface AcpSession {
  /** The agent's advertised slash commands, from `available_commands_update`. */
  availableCommands: AvailableCommand[];
}

/** The session state before the agent advertises anything. */
export const emptySession: AcpSession = { availableCommands: [] };

/**
 * Fold one session update into `session`, returning a NEW snapshot when it carries latent
 * session state, or the SAME reference otherwise (mirroring `foldIntoLog`'s no-op skip, so
 * the store bails on `Object.is` and does not re-render). Today only `available_commands_update`
 * is latent — a `plan` / `current_mode_update` fold joins it in a later phase.
 */
export function foldIntoSession(session: AcpSession, note: SessionNotification): AcpSession {
  const update = note.update;
  switch (update.sessionUpdate) {
    case "available_commands_update":
      return { ...session, availableCommands: update.availableCommands };
    default:
      return session;
  }
}
