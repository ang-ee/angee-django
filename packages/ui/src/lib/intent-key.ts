import * as React from "react";
import {
  AuthoredEnvelopeError,
  ServerUnavailableError,
  hasGraphQLAnswer,
  publicGraphQLErrorsFromUnknown,
  stableKey,
} from "@angee/refine";

import { createClientKey } from "./client-key";

/**
 * What the last answer under a kept key says about its write:
 * - `unconfirmed`: an attempt's answer was lost (no GraphQL response came back), so it
 *   may have been written; a later attempt that is never sent leaves it so;
 * - `not-sent`: the connection boundary refused the request before sending it;
 * - `denied`: the server refused who may write, before the write read its key;
 * - `denied-after-unconfirmed`: the same refusal, after an attempt under this key was
 *   unconfirmed, so that earlier attempt may already have been written.
 */
export type IntentKeyState = "unconfirmed" | "not-sent" | "denied" | "denied-after-unconfirmed";

/** One scope's request identities, from {@link useIntentKeys}. */
export interface IntentKeys {
  /** The key stored for `intent`, else a new one, stored for it. Call it when sending, not while rendering. */
  keyFor: (intent: unknown) => string;
  /** Send `intent` with its key, settle the key by the answer, and return or rethrow what `fire` does. */
  run: <T>(intent: unknown, fire: (key: string) => Promise<T>) => Promise<T>;
  /** What the last answer under the kept key for `intent` said, or null when no key is kept for it. */
  state: (intent: unknown) => IntentKeyState | null;
}

interface IntentKeyEntry {
  key: string;
  state: IntentKeyState | null;
  /** An attempt under this key lost its answer: it may have been written. */
  uncertain: boolean;
}

/** Bounded, oldest first: an intent holds message text, and it is never persisted. */
const MAX_ENTRIES = 100;

/**
 * Answers a write refuses before it reads its key: who may write is decided first, so an
 * earlier attempt under the same key may still have been written, and a later attempt
 * after the refusal is lifted must replay it rather than write again.
 */
const REFUSED_BEFORE_THE_KEY: ReadonlySet<string> = new Set([
  "PERMISSION_DENIED", "FORBIDDEN", "UNAUTHENTICATED", "VIEW_AS_READ_ONLY",
]);

const store = {
  actor: null as string | null,
  entries: new Map<string, IntentKeyEntry>(),
  revision: 0,
  listeners: new Set<() => void>(),
};

function changed(): void {
  store.revision += 1;
  store.listeners.forEach((listener) => listener());
}

/**
 * Tell the store whose keys it holds: the runtime's signed-in person. A change of person
 * forgets every key, so one person's message text does not stay in memory after a sign-out.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function setIntentKeyActor(actor: string): void {
  if (store.actor === actor) return;
  const hadEntries = store.entries.size > 0;
  store.actor = actor;
  store.entries.clear();
  if (hadEntries) changed();
}

function entryId(scope: string, intent: unknown): string {
  // react-query's stable hash: object keys sorted, so their order never changes the key.
  return `${scope}\u0000${stableKey(intent)}`;
}

function entryFor(scope: string, intent: unknown): IntentKeyEntry {
  const id = entryId(scope, intent);
  const stored = store.entries.get(id);
  if (stored) return stored;
  const entry: IntentKeyEntry = { key: createClientKey(scope), state: null, uncertain: false };
  store.entries.set(id, entry);
  while (store.entries.size > MAX_ENTRIES) store.entries.delete(store.entries.keys().next().value!);
  return entry;
}

/** The state a failure leaves on a kept key, or null when its answer releases the key. */
function keptState(error: unknown, entry: IntentKeyEntry): IntentKeyState | null {
  if (error instanceof ServerUnavailableError) return entry.uncertain ? "unconfirmed" : "not-sent";
  if (!(error instanceof AuthoredEnvelopeError) && !hasGraphQLAnswer(error)) return "unconfirmed";
  const codes = error instanceof AuthoredEnvelopeError
    ? [error.code]
    : publicGraphQLErrorsFromUnknown(error).map((item) => item.extensions.code);
  if (!codes.some((code) => typeof code === "string" && REFUSED_BEFORE_THE_KEY.has(code))) return null;
  return entry.uncertain ? "denied-after-unconfirmed" : "denied";
}

/** Keep `entry` with `state`, or release it on null. */
function settle(scope: string, intent: unknown, entry: IntentKeyEntry, state: IntentKeyState | null): void {
  const id = entryId(scope, intent);
  // Released, evicted or forgotten meanwhile (another attempt's answer, a new person).
  if (store.entries.get(id) !== entry) return;
  if (state === null) store.entries.delete(id);
  else {
    entry.state = state;
    entry.uncertain ||= state === "unconfirmed";
  }
  changed();
}

function subscribe(listener: () => void): () => void {
  store.listeners.add(listener);
  return () => { store.listeners.delete(listener); };
}

function revision(): number {
  return store.revision;
}

/**
 * One request identity per write intent, for a write the server replays by its key: the
 * same intent sent again carries the same key, so the server writes it once. The keys
 * live in memory for the page's lifetime, keyed by `scope` and the intent's canonical form
 * (object keys sorted), for the signed-in person; they outlive the surface that minted
 * them, are never persisted, and are forgotten when the person changes. So "sending again
 * will not write twice" holds from this page only: a reload or a second tab mints anew.
 *
 * `run` settles a key by the write's answer: any answer releases it (a result, ok or
 * refused; a GraphQL error; an in-band envelope refusal), except a refusal of who may
 * write, which keeps it. A lost answer (no GraphQL response came back) keeps it, as does
 * a request the connection boundary refused before sending. A surface unmounting never
 * releases a key. Every error is rethrown, so the surface's own owner reports it.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function useIntentKeys(scope: string): IntentKeys {
  // Re-render on a settled key, so a surface reading `state` shows the new one.
  React.useSyncExternalStore(subscribe, revision, revision);
  return React.useMemo<IntentKeys>(() => ({
    keyFor: (intent) => entryFor(scope, intent).key,
    run: async (intent, fire) => {
      const entry = entryFor(scope, intent);
      let answer;
      try {
        answer = await fire(entry.key);
      } catch (error) {
        settle(scope, intent, entry, keptState(error, entry));
        throw error;
      }
      settle(scope, intent, entry, null);
      return answer;
    },
    state: (intent) => store.entries.get(entryId(scope, intent))?.state ?? null,
  }), [scope]);
}
