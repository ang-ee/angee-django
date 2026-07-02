import { useMemo } from "react";
import { hashKey } from "@tanstack/react-query";

/** Return react-query's stable hash for dependency keys derived from values. */
export function stableKey(value: unknown): string {
  return hashKey([value]);
}

/** Stabilize a string array by contents so hook inputs do not churn per render. */
export function useStableArray(items: readonly string[]): readonly string[] {
  const key = items.join("\u0001");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => items, [key]);
}

/** Stabilize a value by JSON contents, coalescing undefined to a fallback. */
export function useStableValue<T>(value: T | undefined, fallback: T): T {
  const resolved = value ?? fallback;
  const key = stableKey(resolved);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => resolved, [key]);
}

/** Stabilize variables passed to refine custom operations. */
export function useStableVariables<T extends Record<string, unknown>>(
  variables: T | undefined,
): T {
  return useStableValue(variables, {} as T);
}
