// Normalize a `{ ok, message }` action-mutation outcome into the record-action
// contract the rendered binding's action bar expects: a returned string is a
// success toast, a thrown error is an error toast.

/** The shape every `{ ok, message }` action mutation returns. */
interface ActionOutcome {
  ok: boolean;
  message: string;
}

/**
 * Turn an action outcome into the action-bar contract: a business failure
 * (`ok: false`) throws (→ error toast), a success returns its `message`
 * (→ success toast), and a missing outcome (the mutation already threw, or
 * returned nothing) yields `undefined` (→ no toast). Without this, handlers that
 * `return result.message` regardless of `ok` show a *success* toast carrying a
 * failure message.
 */
export function runActionResult(
  outcome: ActionOutcome | null | undefined,
): string | undefined {
  if (!outcome) return undefined;
  if (!outcome.ok) throw new Error(outcome.message);
  return outcome.message;
}
