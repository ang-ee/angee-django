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

/** Stabilize a variables object by its serialized contents. */
export function useStableVariables<T extends Record<string, unknown>>(
  variables: T | undefined,
): T {
  const key = JSON.stringify(variables ?? {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => variables ?? ({} as T), [key]);
}

/** Stabilize aggregate measures by their serialized contents. */
export function useStableMeasures(
  measures: readonly AggregateMeasure[] | undefined,
): readonly AggregateMeasure[] {
  const key = JSON.stringify(measures ?? NO_MEASURES);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => measures ?? NO_MEASURES, [key]);
}
