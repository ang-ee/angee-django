import type { ReactNode } from "react";

/**
 * The node, or null when it shows nothing: null, undefined, false and the empty
 * string. The one rule behind a tooltip's label and a disabled reason: an empty
 * reason is no reason.
 */
export function shownContent(node: ReactNode): ReactNode {
  return node === null || node === undefined || node === false || node === "" ? null : node;
}
