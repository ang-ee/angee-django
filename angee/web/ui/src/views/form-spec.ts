import * as React from "react";
import type { CrudFilter, CrudOperators } from "@refinedev/core";

import { useAppRuntime, type WidgetMap } from "../runtime";
import {
  isWidgetDefinition,
  type WidgetOption,
} from "../widgets";
import {
  emptyValueForField,
  type MutationDialogField,
  type MutationDialogRelation,
} from "./MutationDialog";
import type { RelationCreateConfig } from "./RelationPicker";

export type FormSpecFieldType =
  | "string"
  | "integer"
  | "number"
  | "boolean"
  | "object"
  | "array"
  | "any";

export type FormSpecRelationCreate = Pick<RelationCreateConfig, "resource">;

/**
 * Descriptor produced from a backend-emitted JSON form schema.
 * `type`/`properties`/`required`/`items`/`enum`/`const` are the recursive schema
 * vocabulary. Presentation extensions live on each property: string-only
 * `widget`/`label`/`description`/`placeholder`, `readOnly`, JSON `defaultValue`,
 * string-labelled `options`, and the pure-data `relation` config. A property's
 * key becomes the descriptor's `name`; no function-valued extension is admitted.
 * The `rows` widget id is a deliberate forward reference registered next slice.
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

const CRUD_FILTER_OPERATORS: ReadonlySet<string> = new Set<CrudOperators>([
  "eq",
  "ne",
  "eqs",
  "nes",
  "lt",
  "gt",
  "lte",
  "gte",
  "in",
  "nin",
  "ina",
  "nina",
  "contains",
  "ncontains",
  "containss",
  "ncontainss",
  "between",
  "nbetween",
  "null",
  "nnull",
  "startswith",
  "nstartswith",
  "startswiths",
  "nstartswiths",
  "endswith",
  "nendswith",
  "endswiths",
  "nendswiths",
  "or",
  "and",
]);

/** Deserialize a backend-owned form spec through the composed widget registry. */
export function deserializeFormSpec(
  value: unknown,
  widgets: WidgetMap,
): readonly FormSpecFieldDescriptor[] {
  return deserializeObjectFields(value, widgets, "form spec");
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
  const payloadValues = isRecord(payload) ? payload : {};
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
  value: unknown,
  widgets: WidgetMap,
  path: string,
): readonly FormSpecFieldDescriptor[] {
  const schema = recordValue(value, path);
  const type = schema.type ?? "object";
  if (type !== "object") {
    throw new Error(`Invalid ${path}: root type must be "object".`);
  }
  const properties = recordValue(
    schema.properties ?? {},
    `${path}.properties`,
  );
  const required = stringSet(schema.required, `${path}.required`);
  return Object.entries(properties).map(([name, field]) =>
    deserializeField(name, field, required.has(name), widgets, path),
  );
}

function deserializeField(
  name: string,
  value: unknown,
  required: boolean,
  widgets: WidgetMap,
  parentPath: string,
): FormSpecFieldDescriptor {
  const path = parentPath === "form spec" ? name : `${parentPath}.${name}`;
  const field = recordValue(value, `form spec field "${path}"`);
  const type = fieldType(field.type, path);
  const rowTemplate = rowTemplateFrom(field, widgets, path);
  const relation = relationFrom(field.relation, path);
  const options = optionsFrom(field, path);
  const authoredWidget = optionalString(field.widget, `${path}.widget`);
  const label = optionalString(field.label, `${path}.label`);
  const description = optionalString(field.description, `${path}.description`);
  const placeholder = optionalString(field.placeholder, `${path}.placeholder`);
  const readOnly = optionalBoolean(field.readOnly, `${path}.readOnly`);
  if (rowTemplate && authoredWidget && authoredWidget !== "rows") {
    throw new Error(
      `Invalid form spec field "${path}": an array of objects uses widget "rows".`,
    );
  }
  const widget = rowTemplate
    ? "rows"
    : authoredWidget ??
      (relation ? "many2one" : options ? "select" : TYPE_WIDGETS[type]);
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
    ...(Object.hasOwn(field, "defaultValue")
      ? { defaultValue: field.defaultValue }
      : {}),
    ...(options ? { options } : {}),
    ...(relation ? { relation } : {}),
    ...(rowTemplate ? { rowTemplate } : {}),
  };
}

function rowTemplateFrom(
  field: Record<string, unknown>,
  widgets: WidgetMap,
  path: string,
): readonly FormSpecFieldDescriptor[] | undefined {
  if (field.type !== "array" || !isRecord(field.items)) return undefined;
  if (field.items.type !== "object") return undefined;
  return deserializeObjectFields(field.items, widgets, path);
}

function relationFrom(
  value: unknown,
  path: string,
): MutationDialogRelation | undefined {
  if (value === undefined) return undefined;
  const relation = recordValue(value, `${path}.relation`);
  const resource = requiredString(relation.resource, `${path}.relation.resource`);
  const labelField = optionalString(
    relation.labelField,
    `${path}.relation.labelField`,
  );
  const filters = filtersFrom(relation.filters, `${path}.relation.filters`);
  const create = createFrom(relation.create, `${path}.relation.create`);
  return {
    resource,
    ...(labelField ? { labelField } : {}),
    ...(filters ? { filters } : {}),
    ...(create ? { create } : {}),
  };
}

function createFrom(
  value: unknown,
  path: string,
): FormSpecRelationCreate | undefined {
  if (value === undefined) return undefined;
  const create = recordValue(value, path);
  return { resource: requiredString(create.resource, `${path}.resource`) };
}

function filtersFrom(
  value: unknown,
  path: string,
): readonly CrudFilter[] | undefined {
  if (value === undefined) return undefined;
  if (!Array.isArray(value)) {
    throw new Error(`Invalid ${path}: expected an array.`);
  }
  for (const [index, filter] of value.entries()) {
    const item = recordValue(filter, `${path}.${index}`);
    const operator = crudFilterOperator(
      item.operator,
      `${path}.${index}.operator`,
    );
    if (operator === "and" || operator === "or") {
      filtersFrom(item.value, `${path}.${index}.value`);
    } else {
      requiredString(item.field, `${path}.${index}.field`);
    }
  }
  return value as readonly CrudFilter[];
}

function crudFilterOperator(value: unknown, path: string): CrudOperators {
  if (typeof value !== "string" || !CRUD_FILTER_OPERATORS.has(value)) {
    throw new Error(
      `Invalid ${path}: unknown Refine CRUD operator "${String(value)}".`,
    );
  }
  return value as CrudOperators;
}

function optionsFrom(
  field: Record<string, unknown>,
  path: string,
): readonly WidgetOption[] | undefined {
  if (field.options !== undefined) {
    if (!Array.isArray(field.options)) {
      throw new Error(`Invalid ${path}.options: expected an array.`);
    }
    return field.options.map((value, index) => {
      const option = recordValue(value, `${path}.options.${index}`);
      const disabled = optionalBoolean(
        option.disabled,
        `${path}.options.${index}.disabled`,
      );
      return {
        value: requiredString(option.value, `${path}.options.${index}.value`),
        label: requiredString(option.label, `${path}.options.${index}.label`),
        ...(disabled ? { disabled: true } : {}),
      };
    });
  }
  if (!Array.isArray(field.enum)) return undefined;
  return field.enum.map((value) => {
    if (typeof value !== "string") {
      throw new Error(
        `Invalid ${path}.enum: form-spec select values must be strings.`,
      );
    }
    return { value, label: value };
  });
}

function fieldType(value: unknown, path: string): FormSpecFieldType {
  if (value === undefined) return "any";
  if (
    value === "string" ||
    value === "integer" ||
    value === "number" ||
    value === "boolean" ||
    value === "object" ||
    value === "array" ||
    value === "any"
  ) {
    return value;
  }
  throw new Error(`Invalid form spec field "${path}": unsupported type "${String(value)}".`);
}

function stringSet(value: unknown, path: string): ReadonlySet<string> {
  if (value === undefined) return new Set();
  if (!Array.isArray(value) || value.some((entry) => typeof entry !== "string")) {
    throw new Error(`Invalid ${path}: expected an array of field names.`);
  }
  return new Set(value);
}

function recordValue(value: unknown, path: string): Record<string, unknown> {
  if (!isRecord(value)) {
    throw new Error(`Invalid ${path}: expected an object.`);
  }
  return value;
}

function requiredString(value: unknown, path: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`Invalid ${path}: expected a non-empty string.`);
  }
  return value;
}

function optionalString(value: unknown, path: string): string | undefined {
  if (value === undefined) return undefined;
  return requiredString(value, path);
}

function optionalBoolean(value: unknown, path: string): boolean | undefined {
  if (value === undefined) return undefined;
  if (typeof value !== "boolean") {
    throw new Error(`Invalid ${path}: expected a boolean.`);
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
