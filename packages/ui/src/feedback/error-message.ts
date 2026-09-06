import { errorFromUnknown } from "../data/errors";

/**
 * The safe message of an ordinary `Error` or recognized GraphQL transport
 * container, or `fallback` for opaque values. The one owner for user-facing
 * catch-site copy that rendered surfaces would otherwise re-inline at every
 * action boundary.
 */
export function errorMessage(caught: unknown, fallback: string): string {
  if (!(caught instanceof Error) && (caught == null || typeof caught !== "object")) return fallback;
  return errorFromUnknown(caught)?.message ?? fallback;
}
