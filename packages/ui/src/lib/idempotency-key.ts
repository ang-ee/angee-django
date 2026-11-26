import * as React from "react";
import { failedWriteOutcome, type FailedWriteOutcome } from "@angee/refine";

import { createClientKey } from "./client-key";

/** What a draft whose key is kept can tell the person. */
export interface IdempotencyKeyState {
  /**
   * What its last attempt told ({@link failedWriteOutcome}): `not-sent`, `unconfirmed` (it may
   * have been written), `refused`, `denied`, or `conflict`: the key is spent on an earlier version
   * the server wrote, so the draft's next send starts a new draft with a new key.
   */
  outcome: FailedWriteOutcome;
  /** An attempt under the key may have been written (one was unconfirmed), whatever the last said. */
  uncertain: boolean;
}

/** The keyed writes of one app runtime, by draft scope ({@link useIdempotencyKeys}). */
export interface IdempotencyKeys {
  /**
   * Send the draft `scope` names with its idempotency key, minted at its first attempt. A
   * confirmed answer (`confirmed`, by default any it resolves) ends the draft and its key. Any
   * other outcome keeps the key, so the next attempt of the draft carries it, whatever its
   * content became; its outcome is the draft's state. A `conflict` (the key is spent on an
   * earlier version the server wrote) ends the draft at its next send, which mints a new key:
   * the caller says what happened and leaves the rest to the owner. Every error is rethrown.
   */
  send: <T>(scope: string, fire: (key: string) => Promise<T>, confirmed?: (answer: T) => boolean) => Promise<T>;
  /** The person gives the draft up: it ends, unless an attempt may have been written or is still on its way (its key then stays for the scope). */
  discard: (scope: string) => void;
}

interface Draft {
  key: string;
  /** Null until an attempt is answered; a new object on each change, as its readers compare it. */
  state: IdempotencyKeyState | null;
  /** Attempts sent and not yet answered: until they are, the draft may have been written. */
  inFlight: number;
}

/** Whether an attempt under the draft's key may have been written, so it must keep its key. */
function mayBeWritten(draft: Draft): boolean {
  return draft.inFlight > 0 || Boolean(draft.state?.uncertain);
}

/**
 * Drafts the store keeps beyond those an attempt may have written, oldest first. A draft holds a
 * scope and a key, never content; one that may have been written is never dropped, so the bound
 * costs no safety, only the key of an old refused draft (whose write the server never claimed).
 */
const SETTLED_DRAFTS = 100;

/**
 * The drafts of one app runtime, held by its root `AppRuntimeProvider` as the query client is held
 * by its provider: a second app on the page has its own, and a change of signed-in person forgets
 * every draft. In memory only, so "sending again writes once" holds from this page.
 */
export class IdempotencyKeyStore {
  private actor: string | null = null;
  private readonly drafts = new Map<string, Draft>();
  private readonly listeners = new Map<string, Set<() => void>>();

  /** Hold the drafts of `actor`: another person forgets them all. */
  setActor(actor: string): void {
    if (this.actor === actor) return;
    this.actor = actor;
    const scopes = [...this.drafts.keys()];
    this.drafts.clear();
    scopes.forEach((scope) => this.notify(scope));
  }

  state(scope: string): IdempotencyKeyState | null {
    return this.drafts.get(scope)?.state ?? null;
  }

  subscribe(scope: string, listener: () => void): () => void {
    const listeners = this.listeners.get(scope) ?? new Set();
    listeners.add(listener);
    this.listeners.set(scope, listeners);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0 && this.listeners.get(scope) === listeners) this.listeners.delete(scope);
    };
  }

  /** The draft `scope` names, minted when it has none or when its key is spent on a conflict. */
  draft(scope: string): Draft {
    const held = this.drafts.get(scope);
    if (held && held.state?.outcome !== "conflict") return held;
    if (held) this.drafts.delete(scope);
    const draft: Draft = { key: createClientKey(scope), state: null, inFlight: 0 };
    this.drafts.set(scope, draft);
    const settled = [...this.drafts].filter(([, entry]) => !mayBeWritten(entry));
    for (const [oldest] of settled.slice(0, Math.max(0, settled.length - SETTLED_DRAFTS))) this.drafts.delete(oldest);
    return draft;
  }

  /** Keep `draft` with `outcome`, or end it on null; a draft replaced meanwhile is left alone. */
  settle(scope: string, draft: Draft, outcome: FailedWriteOutcome | null): void {
    if (this.drafts.get(scope) !== draft) return;
    if (outcome === null) this.drafts.delete(scope);
    else draft.state = { outcome, uncertain: Boolean(draft.state?.uncertain) || outcome === "unconfirmed" };
    this.notify(scope);
  }

  /** Give the draft up, unless an attempt may have been written (one was lost, or is on its way). */
  discard(scope: string): void {
    const draft = this.drafts.get(scope);
    if (!draft || mayBeWritten(draft)) return;
    this.drafts.delete(scope);
    this.notify(scope);
  }

  private notify(scope: string): void {
    this.listeners.get(scope)?.forEach((listener) => listener());
  }
}

/**
 * The store a root `AppRuntimeProvider` creates and its descendants share.
 * @kind null
 * @reason Provider infrastructure: the app runtime's idempotency keys.
 */
export const IdempotencyKeyStoreContext = React.createContext<IdempotencyKeyStore | null>(null);

function requireStore(store: IdempotencyKeyStore | null): IdempotencyKeyStore {
  if (!store) throw new Error("useIdempotencyKeys must be used under AppRuntimeProvider.");
  return store;
}

/**
 * One idempotency key per draft of a keyed write, for the app runtime: a draft is named by its
 * scope (the surface and what it writes to: a record's chatter box, a dialog for one action on
 * one record, a new-record form), not by a component, so a dialog closed and reopened, or a page
 * left and returned to, meets the same key while an attempt may have been written. The key lives
 * from the draft's first attempt until a confirmed answer ends it, or the person gives it up with
 * nothing possibly written (`discard`). What an error says never releases it: a retry of the
 * draft, edited or not, carries the same key, and the server writes it, replays it (the same
 * content under a key it spent), or answers `conflict` (other content under it), after which the
 * draft's next send starts a new draft. In memory, per signed-in person: sending again writes
 * once from this page only. It needs the app's `AppRuntimeProvider`, and says so when a keyed
 * write is sent without one.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function useIdempotencyKeys(): IdempotencyKeys {
  const held = React.useContext(IdempotencyKeyStoreContext);
  return React.useMemo<IdempotencyKeys>(() => ({
    send: async (scope, fire, confirmed = () => true) => {
      const store = requireStore(held);
      const draft = store.draft(scope);
      draft.inFlight += 1;
      let answer;
      try {
        answer = await fire(draft.key);
      } catch (error) {
        draft.inFlight -= 1;
        store.settle(scope, draft, failedWriteOutcome(error));
        throw error;
      }
      draft.inFlight -= 1;
      store.settle(scope, draft, confirmed(answer) ? null : "refused");
      return answer;
    },
    discard: (scope) => requireStore(held).discard(scope),
  }), [held]);
}

/**
 * What the draft `scope` names can tell the person, or null while it has no answered attempt
 * outstanding; a surface that shows it re-renders when it changes, and only then.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function useIdempotencyKeyState(scope: string): IdempotencyKeyState | null {
  const store = requireStore(React.useContext(IdempotencyKeyStoreContext));
  const subscribe = React.useCallback((listener: () => void) => store.subscribe(scope, listener), [scope, store]);
  const read = React.useCallback(() => store.state(scope), [scope, store]);
  return React.useSyncExternalStore(subscribe, read, read);
}

