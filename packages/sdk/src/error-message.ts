/**
 * The message of a thrown `Error`, or `fallback` for any non-Error value. The
 * one owner for the `caught instanceof Error ? caught.message : fallback` shape
 * that addons would otherwise re-inline at every catch site.
 */
export function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback;
}
