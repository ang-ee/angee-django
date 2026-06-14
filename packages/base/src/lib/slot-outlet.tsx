import { Fragment, isValidElement, type ReactElement, type ReactNode } from "react";
import type { SlotContribution } from "@angee/sdk";

/**
 * Renders the contributions of a composed slot (`useSlot(name)`), in their
 * merged order. Strings/numbers wrap in a `<span>`, elements pass through, and
 * arrays flatten — so a host or addon contributes any renderable to a named
 * seam. The one owner shared by every base surface that exposes a slot (login
 * card, record chrome, …).
 */
export function SlotOutlet({
  entries,
}: {
  entries: readonly SlotContribution[];
}): ReactElement | null {
  const nodes = entries.flatMap((entry) => slotNode(entry.content, entry.id));
  return nodes.length > 0 ? <>{nodes}</> : null;
}

/** Whether any contribution would render a node (so the host can omit an empty wrapper). */
export function slotEntriesHaveContent(
  entries: readonly SlotContribution[],
): boolean {
  return entries.some((entry) => slotNode(entry.content, entry.id).length > 0);
}

function slotNode(value: unknown, key: string): ReactNode[] {
  if (value == null || typeof value === "boolean") return [];
  if (typeof value === "string" || typeof value === "number") {
    return [<span key={key}>{value}</span>];
  }
  if (isValidElement(value)) return [<Fragment key={key}>{value}</Fragment>];
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => slotNode(item, `${key}:${index}`));
  }
  return [];
}
