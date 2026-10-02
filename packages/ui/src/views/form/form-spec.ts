import * as React from "react";
import { stableSerialize } from "@angee/refine";

import { useAppRuntime, type WidgetMap } from "../../runtime";
import {
  isWidgetDefinition,
  type WidgetOption,
} from "../../widgets";
import type { DescriptorField } from "./DescriptorFieldList";
import { emptyValueForField } from "./field-values";
import type { RelationCreateConfig } from "../relation/RelationPicker";
import { statusLabel } from "../../lib/labels";
import { titleCase } from "../../lib/titleCase";
import { parseFormSpec, parseFormSpecPayload, type FormSpecWire, type FormSpecFieldType } from "./form-spec-schema";
export type { FormSpecFieldType } from "./form-spec-schema";

export type FormSpecRelationCreate = Pick<
  RelationCreateConfig,
  "resource" | "defaultValues"
> & {
  actionLabel?: string;
  title?: string;
};

/**
 * Descriptor produced from a backend-emitted JSON form schema.
 * `type`/`properties`/`required`/`items`/`enum`/`const` are the recursive schema
 * vocabulary. Presentation extensions live on each property: string-only
 * `widget`/`label`/`description`/`placeholder` (`label` overrides JSON Schema
 * `title`; an undeclared label is humanized from the field name), list
 * `addLabel`/`removeLabel`,
 * `readOnly`, `hidden` (retained in values without a control), JSON `defaultValue`
 * (overriding the standard schema `default` when both are supplied),
 * string-labelled `options` (or `oneOf` constant titles, then humanized enum
 * values), and the pure-data `relation` config. The backend supplies relation
 * model names as titles when no field title is declared. A property's
 * key becomes the descriptor's `name`; no function-valued extension is admitted.
 * Arrays of objects default to the registered fixed-N `rows` view composer;
 * a named widget must declare that it accepts their parsed row template.
 * Properties and items may reference root-local `$defs` or `definitions`;
 * reference siblings override presentation metadata on the referenced schema.
 * A nullable `anyOf` with one non-null alternative projects that alternative's
 * controls while retaining the outer annotations and nullability.
 * String-valued choice widgets use a descriptor codec for JSON-valued choices;
 * the form retains the original JSON types throughout editing and submission.
 */
export interface FormSpecFieldDescriptor extends DescriptorField {
  /** Form layout intent; ordinary forms and unspecified fields remain inputs. */
  layout?: "context" | "input";
  rowTemplate?: readonly FormSpecFieldDescriptor[];
  objectTemplate?: readonly FormSpecFieldDescriptor[];
  itemTemplate?: FormSpecFieldDescriptor;
  addLabel?: string;
  removeLabel?: string;
  nullable?: boolean;
  omittable?: boolean;
  hasDefault?: boolean;
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
  const schema = parseFormSpec(value);
  return deserializeObjectFields(schema, widgets, "form spec", schema, []);
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

/** Whether the projected descriptors render a control that can display this issue. */
export function formSpecHasControlForPath(fields: readonly FormSpecFieldDescriptor[], path: string): boolean {
  const field = fields.find((field) => path === field.name || path.startsWith(`${field.name}.`));
  return field !== undefined && fieldHasControlForPath(field, path.slice(field.name.length).replace(/^\./, ""));
}

function fieldHasControlForPath(field: FormSpecFieldDescriptor, path: string): boolean {
  if (field.hidden) return false;
  if (!path) return true;
  if (field.objectTemplate) return formSpecHasControlForPath(field.objectTemplate, path);
  if (field.itemTemplate || field.rowTemplate) {
    const item = /^(\d+)(?:\.(.*))?$/.exec(path);
    if (!item) return false;
    const childPath = item[2] ?? "";
    if (field.itemTemplate) return fieldHasControlForPath(field.itemTemplate, childPath);
    return !childPath || formSpecHasControlForPath(field.rowTemplate ?? [], childPath);
  }
  // Atomic widgets (including JSON and subject arrays) display descendant messages themselves.
  return true;
}

/**
 * Seed each declared form-spec field from its matching payload key, followed by
 * its schema default and then the shared descriptor-kind empty value. The form
 * spec remains the whitelist: payload keys absent from it are ignored. An
 * authored structured value retains omitted optional children instead of
 * inventing invalid empty placeholders inside that retained object.
 */
export function formSpecInitialValues(
  fields: readonly FormSpecFieldDescriptor[],
  payload: unknown,
): Record<string, unknown> {
  return formSpecInitialValuesFrom(fields, payload, false);
}

function formSpecInitialValuesFrom(
  fields: readonly FormSpecFieldDescriptor[],
  payload: unknown,
  preserveOptionalOmission: boolean,
): Record<string, unknown> {
  const payloadValues = parseFormSpecPayload(payload);
  const values: Record<string, unknown> = {};
  for (const field of fields) {
    if (Object.hasOwn(payloadValues, field.name)) {
      const payloadValue = payloadValues[field.name];
      if (isFormSpecValueCompatible(field, payloadValue)) {
        values[field.name] = field.objectTemplate && payloadValue && typeof payloadValue === "object" && !Array.isArray(payloadValue)
          ? formSpecInitialValuesFrom(field.objectTemplate, payloadValue, true)
          : field.itemTemplate?.objectTemplate && Array.isArray(payloadValue)
            ? payloadValue.map((item) => item && typeof item === "object" && !Array.isArray(item)
              ? formSpecInitialValuesFrom(field.itemTemplate!.objectTemplate!, item, true)
              : item)
            : payloadValue;
        continue;
      }
    }
    if (field.hasDefault) {
      values[field.name] = field.defaultValue;
      continue;
    }
    if (!field.presenceRequired
        && (field.required || (!field.omittable && !preserveOptionalOmission))) {
      values[field.name] = initialFormSpecValue(field);
    }
  }
  return values;
}

/** Keep retained payloads from passing values to widgets that cannot render them. */
function isFormSpecValueCompatible(
  field: FormSpecFieldDescriptor,
  value: unknown,
): boolean {
  if (value === null) return Boolean(field.nullable);
  switch (field.kind) {
    case "string": return typeof value === "string";
    case "integer": return typeof value === "number" && Number.isInteger(value);
    case "number": return typeof value === "number" && Number.isFinite(value);
    case "boolean": return typeof value === "boolean";
    case "object": return typeof value === "object" && !Array.isArray(value);
    case "array": return Array.isArray(value);
    case "any": return true;
    default: return false;
  }
}

/** Seed one present form-spec value without conflating omission with null. */
export function initialFormSpecValue(field: FormSpecFieldDescriptor): unknown {
  if (field.hasDefault) return field.defaultValue;
  if (field.nullable) return null;
  if (field.objectTemplate) return formSpecInitialValues(field.objectTemplate, {});
  return emptyValueForField(field);
}

/** Remove omitted descriptor keys while preserving explicit JSON values. */
export function normalizeFormSpecValues(
  fields: readonly FormSpecFieldDescriptor[],
  values: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  return Object.fromEntries(fields.flatMap((field) => {
    if (!Object.hasOwn(values, field.name) || values[field.name] === undefined) return [];
    const value = values[field.name];
    if (field.objectTemplate && value && typeof value === "object" && !Array.isArray(value)) {
      return [[field.name, normalizeFormSpecValues(field.objectTemplate, value as Record<string, unknown>)]];
    }
    if (field.itemTemplate?.objectTemplate && Array.isArray(value)) {
      return [[field.name, value.map((item) => item && typeof item === "object" && !Array.isArray(item)
        ? normalizeFormSpecValues(field.itemTemplate!.objectTemplate!, item as Record<string, unknown>)
        : item)]];
    }
    return [[field.name, value]];
  }));
}

/** Resolve a root-local definition; callers decide how to present missing or recursive references. */
export function resolveSchemaReference<T>(
  reference: string,
  root: { $defs?: Readonly<Record<string, T>>; definitions?: Readonly<Record<string, T>> },
  path = "schema",
): T {
  const match = /^#\/(\$defs|definitions)\/([^/]+)$/.exec(reference);
  if (!match) throw new Error(`Invalid ${path}: unsupported reference "${reference}".`);
  const definitions = match[1] === "$defs" ? root.$defs : root.definitions;
  const name = decodeURIComponent(match[2]!).replace(/~1/g, "/").replace(/~0/g, "~");
  if (!definitions || !Object.hasOwn(definitions, name)) {
    throw new Error(`Invalid ${path}: missing reference "${reference}".`);
  }
  return definitions[name]!;
}

/** Resolve references and unambiguous nullable wrappers only for projected nodes. */
function resolveFieldSchema(
  field: FormSpecWire,
  root: FormSpecWire,
  path: string,
  references: readonly FormSpecWire[],
): { field: FormSpecWire; references: readonly FormSpecWire[] } {
  const chain: FormSpecWire[] = [];
  while (true) {
    if (field.$ref !== undefined) {
      const { $ref, ...siblings } = field;
      const target = resolveSchemaReference($ref, root, path);
      if (chain.includes(target)) throw new Error(`Invalid ${path}: cyclic reference "${$ref}".`);
      chain.push(target);
      field = { ...target, ...siblings };
      continue;
    }
    if (!field.anyOf?.some((alternative) => alternative.type === "null")) break;
    const alternatives = field.anyOf.filter((alternative) => alternative.type !== "null");
    if (alternatives.length !== 1) break;
    const { anyOf: _alternatives, ...siblings } = field;
    field = { ...alternatives[0], ...siblings, nullable: true };
  }
  return { field, references: [...references, ...chain] };
}

function assertFiniteTemplate(references: readonly FormSpecWire[], path: string): void {
  if (new Set(references).size !== references.length) {
    throw new Error(`Invalid ${path}: cyclic reference requires an infinite form template.`);
  }
}

function deserializeObjectFields(
  schema: FormSpecWire,
  widgets: WidgetMap,
  path: string,
  root: FormSpecWire,
  references: readonly FormSpecWire[],
): readonly FormSpecFieldDescriptor[] {
  assertFiniteTemplate(references, path);
  const required = new Set(schema.required ?? []);
  const properties = schema.properties ?? {};
  const names = schema.propertyOrder ?? Object.keys(properties);
  if (names.length !== Object.keys(properties).length
      || new Set(names).size !== names.length
      || names.some((name) => !Object.hasOwn(properties, name))) {
    throw new Error(`Invalid ${path}.propertyOrder: every property must be named exactly once.`);
  }
  return names.map((name) =>
    deserializeField(name, properties[name]!, required.has(name), widgets, path, root, references),
  );
}

function deserializeField(
  name: string,
  schema: FormSpecWire,
  required: boolean,
  widgets: WidgetMap,
  parentPath: string,
  root: FormSpecWire,
  ancestors: readonly FormSpecWire[],
): FormSpecFieldDescriptor {
  const path = parentPath === "form spec" ? name : `${parentPath}.${name}`;
  const { field, references } = resolveFieldSchema(schema, root, path, ancestors);
  const type = formSpecFieldType(field.type, field.anyOf);
  const nullable = field.nullable
    || field.type === "null"
    || (Array.isArray(field.type) && field.type.includes("null"))
    || field.anyOf?.some((alternative) => alternative.type === "null");
  const variableList = type === "array" && field.widget === "list";
  const items = type === "array" && field.items && field.layout !== "context"
    ? resolveFieldSchema(field.items, root, `${path}[]`, references)
    : undefined;
  const rowTemplate = items && !variableList
    && formSpecFieldType(items.field.type, items.field.anyOf) === "object"
    ? deserializeObjectFields(items.field, widgets, path, root, items.references)
    : undefined;
  const structuredObject = type === "object" && (field.widget === "object"
    || (field.widget === undefined && field.properties !== undefined));
  const objectTemplate = structuredObject && field.layout !== "context"
    ? deserializeObjectFields(field, widgets, path, root, references)
    : undefined;
  if (variableList && items) assertFiniteTemplate(references, path);
  const itemTemplate = variableList && items
    ? deserializeField("item", items.field, true, widgets, `${path}[]`, root, items.references)
    : undefined;
  const {
    relation, widget: authoredWidget, addLabel, removeLabel,
    description, placeholder, readOnly, hidden, layout,
  } = field;
  const label = field.label ?? field.title ?? titleCase(name);
  const choices = optionsFrom(field);
  const options = choices?.options;
  const widget = rowTemplate
    ? authoredWidget ?? "rows"
    : authoredWidget ?? (relation ? "many2one" : options ? "select" : objectTemplate ? "object" : TYPE_WIDGETS[type]);
  const definition = widgets[widget];
  if (!isWidgetDefinition(definition)) {
    throw new Error(
      `Unknown form spec widget "${widget}" for field "${path}". Register it in AppRuntime.widgets.`,
    );
  }
  if (rowTemplate && !definition.acceptsRowTemplate) {
    throw new Error(
      `Invalid form spec field "${path}": widget "${widget}" does not accept a row template.`,
    );
  }
  return {
    name,
    kind: type,
    widget,
    ...(label ? { label } : {}),
    ...(field.assignmentSubjectKinds ? { assignmentSubjectKinds: field.assignmentSubjectKinds } : {}),
    ...(addLabel ? { addLabel } : {}),
    ...(removeLabel ? { removeLabel } : {}),
    ...(description ? { description } : {}),
    ...(placeholder ? { placeholder } : {}),
    ...(required ? { required: true } : {}),
    ...(field.presenceRequired ? { presenceRequired: true } : {}),
    ...(field.omittable ? { omittable: true } : {}),
    ...(nullable ? { nullable: true } : {}),
    ...(field.minimum !== undefined ? { minimum: field.minimum } : {}),
    ...(field.maximum !== undefined ? { maximum: field.maximum } : {}),
    ...(field.minLength !== undefined ? { minLength: field.minLength } : {}),
    ...(field.maxLength !== undefined ? { maxLength: field.maxLength } : {}),
    ...(field.minItems !== undefined ? { minItems: field.minItems } : {}),
    ...(field.maxItems !== undefined ? { maxItems: field.maxItems } : {}),
    ...(readOnly ? { readOnly: true } : {}),
    ...(hidden ? { hidden: true } : {}),
    ...(layout ? { layout } : {}),
    ...(Object.hasOwn(field, "defaultValue") ? { defaultValue: field.defaultValue, hasDefault: true }
      : Object.hasOwn(field, "default") ? { defaultValue: field.default, hasDefault: true } : {}),
    ...(options ? { options } : {}),
    ...(choices?.valueCodec && (widget === "select" || widget === "selection" || widget === "combobox")
      ? { valueCodec: choices.valueCodec } : {}),
    ...(relation ? { relation } : {}),
    ...(rowTemplate ? { rowTemplate } : {}),
    ...(objectTemplate ? { objectTemplate } : {}),
    ...(itemTemplate ? { itemTemplate } : {}),
  };
}

function formSpecFieldType(
  type: FormSpecWire["type"],
  alternatives: FormSpecWire["anyOf"] = [],
): FormSpecFieldType {
  if (Array.isArray(type)) {
    const nonNull = type.filter((value) => value !== "null");
    return nonNull.length === 1 ? nonNull[0] as FormSpecFieldType : "any";
  }
  if (type) return type === "null" ? "any" : type;
  const alternativeTypes = [...new Set(alternatives
    .map((alternative) => alternative.type)
    .filter((value): value is FormSpecFieldType => (
      typeof value === "string" && value !== "null"
    )))];
  return alternativeTypes.length === 1 ? alternativeTypes[0]! : "any";
}

/** Ordinal control tokens distinguish choices such as the number 1 and string "1". */
function optionsFrom(field: FormSpecWire): Pick<FormSpecFieldDescriptor, "options" | "valueCodec"> | undefined {
  const choices: readonly { value: unknown; label: string; disabled?: boolean }[] | undefined =
    field.options ?? (field.oneOf?.length && field.oneOf.every((choice) => Object.hasOwn(choice, "const"))
    ? field.oneOf.map((choice) => ({ value: choice.const, label: choice.title ?? choiceLabel(choice.const) }))
    : undefined) ?? field.enum?.map((value) => ({ value, label: choiceLabel(value) }));
  if (!choices) return undefined;
  const typed = choices.some(({ value }) => typeof value !== "string");
  const options: readonly WidgetOption[] = choices.map(({ value, label, disabled }, index) => ({
    value: !typed && typeof value === "string" ? value : String(index),
    label, ...(disabled ? { disabled: true } : {}),
  }));
  if (!typed) return { options };
  const keys = choices.map(({ value }) => stableSerialize(value));
  return { options, valueCodec: {
    toControl(value) {
      const index = keys.indexOf(stableSerialize(value));
      return index < 0 ? undefined : String(index);
    },
    fromControl(value) {
      const index = options.findIndex((option) => option.value === value);
      return index < 0 ? undefined : structuredClone(choices[index]!.value);
    },
  } };
}

function choiceLabel(value: unknown): string {
  return typeof value === "string" ? statusLabel(value) : JSON.stringify(value);
}
