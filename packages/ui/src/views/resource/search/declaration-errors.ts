import { developmentMode } from "../../../lib/development-mode";

/** Metadata-dependent declarations fail loudly in development and are omitted in production. */
export function rejectSearchDeclaration(
  kind: "filter option" | "shortcut",
  id: string,
  reason: string,
  reported?: Set<string>,
): false {
  const identity = `Search ${kind} "${id}"`;
  const message = `${identity}: ${reason}.`;
  if (developmentMode()) throw new Error(message);
  if (!reported?.has(identity)) {
    reported?.add(identity);
    console.error(message);
  }
  return false;
}
