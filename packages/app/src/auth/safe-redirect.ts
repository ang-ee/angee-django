/** The origin a rooted value resolves against where no page origin exists (server rendering, tests). */
const FALLBACK_ORIGIN = "https://angee.invalid";

/**
 * Return `value` only when it is a rooted path that stays on this origin,
 * otherwise `null`. This is the open-redirect guard for any
 * post-authentication `next`/redirect target. The value is resolved the way
 * the browser will resolve it, with `new URL(value, location.origin)`, which
 * also strips the tabs and newlines and folds the backslashes that turn
 * `/\t/host` or `/\host` into another origin. Only a same-origin result is
 * accepted, and its path, query and fragment are returned.
 *
 * Client-side validation is defense-in-depth; the server is the real boundary.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function safeRedirectPath(
  value: string | null | undefined,
): string | null {
  if (!value || !value.startsWith("/")) return null;
  const origin = globalThis.location?.origin ?? FALLBACK_ORIGIN;
  let url: URL;
  try {
    url = new URL(value, origin);
  } catch {
    return null;
  }
  return url.origin === origin ? `${url.pathname}${url.search}${url.hash}` : null;
}
