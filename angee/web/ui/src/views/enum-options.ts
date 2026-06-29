import * as React from "react";
import { useAuthoredQuery } from "../data/authored-hooks";
import {
  useModelMetadata,
} from "@angee/resources";

import type { WidgetOption } from "../widgets";
import { BaseImplChoices, type ImplChoice } from "./documents";
import { enumValueLabel } from "./ListInternals";

/**
 * SDL-derived `<select>` options for an enum field, with lower-cased values.
 *
 * An enum reads as the UPPERCASE member name but its create/patch input is a
 * lowercase `String` value, so a bare metadata-driven select submits the member
 * name and the input rejects it. Pair these options with a `createOnly` field so
 * the read casing never round-trips through the select (see the enum read/write
 * pitfall in docs/frontend/guidelines.md). The label is the SDL description where
 * authored, otherwise the humanized member name (`enumValueLabel`).
 */
export function useEnumOptions(resource: string, field: string): readonly WidgetOption[] {
  const metadata = useModelMetadata(resource);
  return React.useMemo<readonly WidgetOption[]>(
    () =>
      (metadata?.fields[field]?.values ?? []).map((value) => ({
        value: value.value.toLowerCase(),
        label: enumValueLabel(value),
      })),
    [metadata, field],
  );
}

export function useImplChoices(resource: string, field: string): readonly ImplChoice[] {
  const { data } = useAuthoredQuery(BaseImplChoices, {
    model: resource,
    field,
  });
  return data?.impl_choices ?? [];
}

export function useImplCategory(resource: string, field: string): (value: unknown) => string {
  const choices = useImplChoices(resource, field);
  return React.useMemo(() => {
    const byKey = new Map(choices.map((choice) => [choice.key, choice.category]));
    return (value: unknown) => byKey.get(String(value)) ?? "";
  }, [choices]);
}

/**
 * A prefill function for an `ImplClassField` select: given the chosen impl key,
 * returns that impl's defaults keyed by field name, ready to pass to a `<Field prefill>`.
 * The server (`impl_choices`) owns the per-impl defaults (merged along the impl MRO).
 * Picking an impl loads its full preset (overwriting those
 * fields, so boolean defaults land too); the backend also materialises them on create.
 */
export function useImplPrefill(
  resource: string,
  field: string,
): (value: unknown) => Record<string, unknown> | undefined {
  const choices = useImplChoices(resource, field);
  return React.useMemo(() => {
    const byKey = new Map(
      choices.map((choice) => [choice.key, choice.defaults]),
    );
    return (value: unknown) => {
      const defaults = byKey.get(String(value));
      if (!defaults) return undefined;
      return defaults as Record<string, unknown>;
    };
  }, [choices]);
}
