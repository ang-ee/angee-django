import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  useModelMetadata,
} from "@angee/metadata";

import { canonicalOptionValue, type WidgetOption } from "../../widgets";
import { useAppRuntime } from "../../runtime";
import { deserializeFormSpec, type FormSpecFieldDescriptor } from "../form/form-spec";
import type { FieldDescriptor } from "../page";
import { BaseImplChoices, type ImplChoice } from "../resource/documents";
import { enumValueLabel } from "../resource/resource-view-list-body";

const EMPTY_IMPL_PREFILL_RESET: Readonly<Record<string, unknown>> = {};

/**
 * SDL-derived `<select>` options for an enum field.
 *
 * An enum reads as the UPPERCASE member name but its create/patch input is a
 * lowercase `String` value, so a bare metadata-driven select submits the member
 * name and the input rejects it. Lowercase is the default; authored actions that
 * accept a GraphQL enum select `casing: "upper"`. Pair CRUD options with a `createOnly` field so
 * the read casing never round-trips through the select (see the enum read/write
 * pitfall in docs/guidelines.md). The label is the SDL description where
 * authored, otherwise the humanized member name (`enumValueLabel`).
 */
export function useEnumOptions(
  resource: string,
  field: string,
  { casing = "lower" }: { casing?: "lower" | "upper" } = {},
): readonly WidgetOption[] {
  const metadata = useModelMetadata(resource);
  return React.useMemo<readonly WidgetOption[]>(
    () =>
      (metadata?.fields[field]?.values ?? []).map((value) => ({
        value: casing === "upper" ? value.value.toUpperCase() : value.value.toLowerCase(),
        label: enumValueLabel(value),
      })),
    [metadata, field, casing],
  );
}

export function useImplChoices(
  resource: string,
  field: string,
  supplied?: readonly ImplChoice[],
): readonly ImplChoice[] {
  const { data } = useAuthoredQuery(BaseImplChoices, {
    model: resource,
    field,
  }, { enabled: supplied == null });
  return supplied ?? data?.impl_choices ?? [];
}

export interface ImplConfigFields {
  fields: readonly (FormSpecFieldDescriptor & Pick<FieldDescriptor, "showWhen" | "resolve">)[];
  hasSchema: (value: unknown) => boolean;
}

/**
 * Project backend-owned implementation config specs into ordinary dotted form
 * fields. The existing FormSpec parser/widget registry remain the only schema
 * engine; choices without a declaration continue to use their native raw JSON
 * field.
 */
export function useImplConfigFields(
  resource: string,
  field: string,
  supplied?: readonly ImplChoice[],
): ImplConfigFields {
  const choices = useImplChoices(resource, field, supplied);
  const { widgets } = useAppRuntime();
  return React.useMemo(() => {
    const options = choices.map((choice) => ({ value: choice.key, label: choice.key }));
    const choiceKey = (value: unknown) => canonicalOptionValue(options, value) ?? String(value);
    const parsed = choices.flatMap((choice) => choice.config_schema == null
      ? []
      : [{ choice, fields: deserializeFormSpec(choice.config_schema, widgets) }]);
    const schemaKeys = new Set(parsed.map(({ choice }) => choice.key));
    const byName = new Map<string, { descriptor: FieldDescriptor; variants: Map<string, FieldDescriptor> }>();
    for (const { choice, fields } of parsed) {
      for (const descriptor of fields) {
        const current = byName.get(descriptor.name);
        if (current) current.variants.set(choice.key, descriptor);
        else byName.set(descriptor.name, {
          descriptor,
          variants: new Map([[choice.key, descriptor]]),
        });
      }
    }
    return {
      fields: [...byName.values()].map(({ descriptor, variants }) => ({
        ...descriptor,
        name: `config.${descriptor.name}`,
        showWhen: (values) => variants.has(choiceKey(values[field])),
        resolve: (values) => {
          const selected = variants.get(choiceKey(values[field])) ?? descriptor;
          return {
            ...selected,
            name: `config.${selected.name}`,
            showWhen: (current) => variants.has(choiceKey(current[field])),
          };
        },
      })),
      hasSchema: (value: unknown) => schemaKeys.has(choiceKey(value)),
    };
  }, [choices, field, widgets]);
}

export function useImplCategory(resource: string, field: string): (value: unknown) => string {
  const choices = useImplChoices(resource, field);
  return React.useMemo(() => {
    const options = choices.map((choice) => ({ value: choice.key, label: choice.key }));
    const byKey = new Map(choices.map((choice) => [choice.key, choice.category]));
    return (value: unknown) => byKey.get(canonicalOptionValue(options, value) ?? String(value)) ?? "";
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
  reset: Readonly<Record<string, unknown>> = EMPTY_IMPL_PREFILL_RESET,
  supplied?: readonly ImplChoice[],
): (value: unknown) => Record<string, unknown> | undefined {
  const choices = useImplChoices(resource, field, supplied);
  return React.useMemo(() => {
    const options = choices.map((choice) => ({ value: choice.key, label: choice.key }));
    const byKey = new Map(
      choices.map((choice) => [choice.key, choice.defaults]),
    );
    return (value: unknown) => {
      const key = canonicalOptionValue(options, value) ?? String(value);
      const defaults = byKey.get(key);
      if (!defaults) return undefined;
      return { ...reset, ...defaults } as Record<string, unknown>;
    };
  }, [choices, reset]);
}
