import { createClientKey } from "../../lib/client-key";
import { invalidFormSubmit, type FormSubmitResult } from "./validation-errors";

/** The editable key is independent of the stable client identity. */
export interface KeyedEntry<TValue> {
  clientId: string;
  key: string;
  value: TValue;
}

/** An array retains authored order even when editable keys are integer strings. */
export type KeyedCollection<TValue> = KeyedEntry<TValue>[];

export interface KeyedCollectionSnapshot<TValue> {
  values: Record<string, TValue>;
  /** Translate client references while assembling the submitted document. */
  keyByClientId: ReadonlyMap<string, string>;
  /** Retain this submit-time map to project returned issue paths to form entries. */
  clientIdByKey: ReadonlyMap<string, string>;
}

/** Load a keyed document using each loaded key as its stable client identity. */
export function keyedCollectionFromRecord<TValue>(
  values: Readonly<Record<string, TValue>>,
): KeyedCollection<TValue> {
  return Object.entries(values).map(([key, value]) => ({ clientId: key, key, value: structuredClone(value) }));
}

/** Append this entry through RHF's native array controls or setValue. */
export function createKeyedEntry<TValue>(value: TValue, key = ""): KeyedEntry<TValue> {
  return { clientId: createClientKey("entry"), key, value: structuredClone(value) };
}

/**
 * Capture one save snapshot. Renaming edits only entry.key; conversion happens
 * here. Duplicate keys return native invalid-submit issues for every affected
 * client id. The caller projects those ids to its current array field paths.
 * Record values follow JavaScript key enumeration; the collection and maps
 * retain authored order. The caller owns other key and reference validation.
 */
export function keyedCollectionToRecord<TValue>(
  entries: readonly KeyedEntry<TValue>[],
): Extract<FormSubmitResult<KeyedCollectionSnapshot<TValue>>, { status: "ok" | "invalid" }> {
  const keyByClientId = new Map<string, string>();
  const clientIdByKey = new Map<string, string>();
  const issues = new Map<string, string[]>();
  for (const { clientId, key } of entries) {
    const previous = clientIdByKey.get(key);
    if (previous !== undefined) {
      const message = `Duplicate collection key: ${key}`;
      issues.set(previous, [message]);
      issues.set(clientId, [message]);
    }
    if (keyByClientId.has(clientId)) issues.set(clientId, ["Duplicate collection identity"]);
    keyByClientId.set(clientId, key);
    clientIdByKey.set(key, clientId);
  }
  if (issues.size) return invalidFormSubmit({ fieldErrors: Object.fromEntries(issues), formErrors: [] });
  const values = Object.fromEntries(entries.map(({ key, value }) => [key, structuredClone(value)]));
  return { status: "ok", data: { values, keyByClientId, clientIdByKey } };
}
