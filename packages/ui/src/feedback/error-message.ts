/**
 * The message of a thrown `Error`, or `fallback` for any non-Error value. The
 * one owner for user-facing catch-site copy that rendered surfaces would
 * otherwise re-inline at every action boundary.
 */
export function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}
