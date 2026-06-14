import { useMemo } from "react";

import type { AggregateMeasure } from "./aggregate-extract";

// Value-equality memoization for the inputs hooks pass to urql and the document
// builder. A view re-creates the `fields` array / `variables` object on every
// render; these keep the reference stable while the *contents* are equal, so a
// document or query is not rebuilt on every render. The lint suppression for
// keying a memo by a derived value (rather than the value itself) lives here,
// in one audited place, instead of being repeated at every call site.

const NO_MEASURES = [] as const satisfies readonly AggregateMeasure[];

/** Stabilize a string array (e.g. field paths) by its joined contents. */
export function useStableArray(items: readonly string[]): readonly string[] {
  const key = items.join("");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => items, [key]);
}

/**
 * Stabilize any value by its serialized contents: hold the reference stable
 * while the JSON of the value (coalesced to `fallback`) is unchanged. The owner
 * of the structural-equality memo for objects/arrays passed to urql and the
 * document builder — `useStableVariables`/`useStableMeasures` are thin defaults
 * over it. (`useStableArray` keeps a cheaper join key for plain string lists.)
 */
export function useStableValue<T>(value: T | undefined, fallback: T): T {
  const resolved = value ?? fallback;
  const key = JSON.stringify(resolved);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => resolved, [key]);
}

/** Stabilize a variables object by its serialized contents. */
export function useStableVariables<T extends Record<string, unknown>>(
  variables: T | undefined,
): T {
  return useStableValue(variables, {} as T);
}

/** Stabilize aggregate measures by their serialized contents. */
export function useStableMeasures(
  measures: readonly AggregateMeasure[] | undefined,
): readonly AggregateMeasure[] {
  return useStableValue(measures, NO_MEASURES);
}
