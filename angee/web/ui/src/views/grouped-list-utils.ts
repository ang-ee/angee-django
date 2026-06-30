import * as React from "react";

/**
 * Group-collapse state shared by every grouped list — the server-driven
 * {@link GroupedList} and the client virtualized flat list alike. A set of the
 * expanded group keys, empty by default so groups start collapsed; toggling is
 * immutable. Stale keys are harmless: they restore a group's state if it
 * reappears after a regrouping, so the set is never pruned.
 */
export function useExpandedKeys(): {
  expandedKeys: ReadonlySet<string>;
  toggle: (key: string) => void;
} {
  const [expandedKeys, setExpandedKeys] = React.useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const toggle = React.useCallback((key: string) => {
    setExpandedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
  return { expandedKeys, toggle };
}
