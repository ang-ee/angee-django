import * as React from "react";

/**
 * Return a value-stable reference: the same object identity is preserved across
 * renders as long as the value is structurally equal (compared by a stable JSON
 * serialisation). A caller that rebuilds an array/object inline every render —
 * e.g. `filters={[...]}` — otherwise forwards a fresh identity into hooks whose
 * effects key on it, which can drive an update loop. This collapses value-equal
 * inputs back to one identity so plausible inline props are safe without every
 * caller memoising.
 *
 * Intended for small, JSON-serialisable config (filters, sorters). Not for
 * values carrying functions or cyclic references.
 */
export function useValueStable<T>(value: T): T {
  const key = stableKey(value);
  const ref = React.useRef<{ key: string; value: T }>({ key, value });
  if (ref.current.key !== key) {
    ref.current = { key, value };
  }
  return ref.current.value;
}

function stableKey(value: unknown): string {
  if (value === undefined) return "\0undefined";
  return JSON.stringify(value, (_k, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(
          Object.entries(v as Record<string, unknown>).sort(([a], [b]) =>
            a < b ? -1 : a > b ? 1 : 0,
          ),
        )
      : v,
  );
}
