import * as React from "react";

import { useAppRuntime, type WidgetMap } from "../../runtime";
import {
  isWidgetDefinition,
  type WidgetOption,
} from "../../widgets";
import {
  emptyValueForField,
  type MutationDialogField,
} from "./MutationDialog";
import type { RelationCreateConfig } from "../relation/RelationPicker";
import { parseFormSpec, parseFormSpecPayload, type FormSpecWire, type FormSpecFieldType } from "./form-spec-schema";
export type { FormSpecFieldType } from "./form-spec-schema";

export type FormSpecRelationCreate = Pick<RelationCreateConfig, "resource">;

/**
 * Descriptor produced from a backend-emitted JSON form schema.
 * `type`/`properties`/`required`/`items`/`enum`/`const` are the recursive schema
 * vocabulary. Presentation extensions live on each property: string-only
 * `widget`/`label`/`description`/`placeholder`, `readOnly`, JSON `defaultValue`,
 * string-labelled `options`, and the pure-data `relation` config. A property's
 * key becomes the descriptor's `name`; no function-valued extension is admitted.
 * Arrays of objects resolve to the registered fixed-N `rows` view composer.
 */
export interface FormSpecFieldDescriptor extends MutationDialogField {
  rowTemplate?: readonly FormSpecFieldDescriptor[];
}

const TYPE_WIDGETS: Readonly<Record<FormSpecFieldType, string>> = {
  string: "text",
  integer: "integer",
  number: "float",
  boolean: "boolean",
  object: "json",
  array: "json",
  any: "json",
};

/** Deserialize a backend-owned form spec through the composed widget registry. */
export function deserializeFormSpec(
  value: unknown,
  widgets: WidgetMap,
): readonly FormSpecFieldDescriptor[] {
  return deserializeObjectFields(parseFormSpec(value), widgets, "form spec");
}

/** Resolve a form spec against the current app's build-time widget registry. */
export function useFormSpecFields(
  value: unknown,
): readonly FormSpecFieldDescriptor[] {
  const { widgets } = useAppRuntime();
  return React.useMemo(
    () => deserializeFormSpec(value, widgets),
    [value, widgets],
  );
}

/**
 * Seed each declared form-spec field from its matching payload key, followed by
 * its schema default and then the shared descriptor-kind empty value. The form
 * spec remains the whitelist: payload keys absent from it are ignored.
 */
export function formSpecInitialValues(
  fields: readonly FormSpecFieldDescriptor[],
  payload: unknown,
): Record<string, unknown> {
  const payloadValues = parseFormSpecPayload(payload);
  const values: Record<string, unknown> = {};
  for (const field of fields) {
    if (Object.hasOwn(payloadValues, field.name)) {
      values[field.name] = payloadValues[field.name];
      continue;
    }
    if (field.defaultValue !== undefined) {
      values[field.name] = field.defaultValue;
      continue;
    }
    values[field.name] = emptyValueForField(field);
  }
  return values;
}

function deserializeObjectFields(
  schema: FormSpecWire,
  widgets: WidgetMap,
  path: string,
): readonly FormSpecFieldDescriptor[] {
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).map(([name, field]) =>
    deserializeField(name, field, required.has(name), widgets, path),
  );
}

function deserializeField(
  name: string,
  field: FormSpecWire,
  required: boolean,
  widgets: WidgetMap,
  parentPath: string,
): FormSpecFieldDescriptor {
  const path = parentPath === "form spec" ? name : `${parentPath}.${name}`;
  const type = field.type ?? "any";
  const rowTemplate = field.type === "array" && field.items?.type === "object"
    ? deserializeObjectFields(field.items, widgets, path)
    : undefined;
  const { relation, widget: authoredWidget, label, description, placeholder, readOnly } = field;
  const options = optionsFrom(field);
  if (rowTemplate && authoredWidget && authoredWidget !== "rows") {
    throw new Error(
      `Invalid form spec field "${path}": an array of objects uses widget "rows".`,
    );
  }
  const widget = rowTemplate
    ? "rows"
    : authoredWidget ?? (relation ? "many2one" : options ? "select" : TYPE_WIDGETS[type]);
  if (!isWidgetDefinition(widgets[widget])) {
    throw new Error(
      `Unknown form spec widget "${widget}" for field "${path}". Register it in AppRuntime.widgets.`,
    );
  }
  return {
    name,
    kind: type,
    widget,
    ...(label ? { label } : {}),
    ...(description ? { description } : {}),
    ...(placeholder ? { placeholder } : {}),
    ...(required ? { required: true } : {}),
    ...(readOnly ? { readOnly: true } : {}),
    ...(Object.hasOwn(field, "defaultValue") ? { defaultValue: field.defaultValue } : {}),
    ...(options ? { options } : {}),
    ...(relation ? { relation } : {}),
    ...(rowTemplate ? { rowTemplate } : {}),
  };
}

function optionsFrom(field: FormSpecWire): readonly WidgetOption[] | undefined {
  if (field.options) {
    return field.options.map(({ value, label, disabled }) => ({
      value, label, ...(disabled ? { disabled: true } : {}),
    }));
  }
  return field.enum?.map((value) => ({ value, label: value }));
}
