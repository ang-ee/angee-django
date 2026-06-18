import * as React from "react";
import { useAuthoredQuery, useModelMetadata } from "@angee/sdk";

import type { WidgetOption } from "../widgets";
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
export function useEnumOptions(model: string, field: string): readonly WidgetOption[] {
  const metadata = useModelMetadata(model);
  return React.useMemo<readonly WidgetOption[]>(
    () =>
      (metadata?.fields[field]?.values ?? []).map((value) => ({
        value: value.value.toLowerCase(),
        label: enumValueLabel(value),
      })),
    [metadata, field],
  );
}

const IMPL_CHOICES_DOCUMENT = `
  query ImplChoices($model: String!, $field: String!) {
    implChoices(model: $model, field: $field) {
      key
      defaults
    }
  }
`;

interface ImplChoicesData {
  implChoices: readonly { key: string; defaults: Record<string, unknown> }[];
}

/**
 * A prefill function for an `ImplClassField` select: given the chosen impl key,
 * returns that impl's defaults keyed by *camelCase form field name*, ready to pass
 * to a `<Field prefill>`. The server (`implChoices`) owns the per-impl defaults
 * (merged along the impl MRO); this only re-keys snake_case model fields to the
 * camelCase the form uses. Defaults seed *blank* siblings, so the user can still
 * override before saving (the backend also materialises them on create).
 */
export function useImplPrefill(
  model: string,
  field: string,
): (value: unknown) => Record<string, unknown> | undefined {
  const { data } = useAuthoredQuery<ImplChoicesData>(IMPL_CHOICES_DOCUMENT, {
    model,
    field,
  });
  return React.useMemo(() => {
    const byKey = new Map(
      (data?.implChoices ?? []).map((choice) => [choice.key, choice.defaults]),
    );
    return (value: unknown) => {
      const defaults = byKey.get(String(value));
      if (!defaults) return undefined;
      return Object.fromEntries(
        Object.entries(defaults).map(([name, seed]) => [snakeToCamel(name), seed]),
      );
    };
  }, [data]);
}

function snakeToCamel(name: string): string {
  return name.replace(/_([a-z0-9])/g, (_match, char: string) => char.toUpperCase());
}
