import { Fragment, isValidElement, type ReactElement, type ReactNode } from "react";

/** A container child as an outlet renders it: an id and renderable content. */
export interface OutletChild {
  id: string;
  content?: unknown;
}

/**
 * Renders a container's children (`useContainer(address)`) in their composed
 * order. Strings and numbers wrap in a `<span>`, elements pass through, and
 * arrays flatten, so an addon contributes any renderable to a named container.
 * The one owner shared by every base surface that exposes a container (login
 * card, record chrome, …).
 */
export function ContainerOutlet({ entries }: { entries: readonly OutletChild[] }): ReactElement | null {
  const nodes = containerContents(entries);
  return nodes.length > 0 ? <>{nodes}</> : null;
}

/** Return container children as raw keyed nodes suitable for renderers or parsers. */
export function containerContents(entries: readonly OutletChild[]): ReactNode[] {
  return entries.flatMap((entry) => childNode(entry.content, entry.id));
}

/** Whether any child would render a node (so the host can omit an empty wrapper). */
export function containerHasContent(entries: readonly OutletChild[]): boolean {
  return containerContents(entries).length > 0;
}

function childNode(value: unknown, key: string): ReactNode[] {
  if (value == null || typeof value === "boolean") return [];
  if (typeof value === "string" || typeof value === "number") {
    return [<span key={key}>{value}</span>];
  }
  if (isValidElement(value)) return [<Fragment key={key}>{value}</Fragment>];
  if (Array.isArray(value)) {
    return value.flatMap((item, index) => childNode(item, `${key}:${index}`));
  }
  return [];
}
