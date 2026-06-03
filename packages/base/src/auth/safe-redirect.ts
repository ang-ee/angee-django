/**
 * Return `value` only when it is a same-site relative path that is safe to
 * redirect a browser to, otherwise `null`. This is the open-redirect guard for
 * any post-authentication `next`/redirect target: a safe value starts with a
 * single `/` and is neither a protocol-relative `//host` nor a backslash `/\`
 * escape (both of which browsers resolve to another origin).
 *
 * Client-side validation is defense-in-depth; the server is the real boundary.
 */
export function safeRedirectPath(
  value: string | null | undefined,
): string | null {
  if (!value || !value.startsWith("/")) return null;
  const second = value[1];
  if (second === "/" || second === "\\") return null;
  return value;
}
