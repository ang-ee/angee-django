import * as React from "react";
import * as v from "valibot";
import Ajv2020 from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import addFormats from "ajv-formats";

import { useAppRuntime, type WidgetMap } from "../../runtime";
import {
  isWidgetDefinition,
  type WidgetOption,
} from "../../widgets";
import type { MutationDialogField } from "./MutationDialog";
import { emptyValueForField } from "./field-values";
import { JsonValueSchema } from "../../widgets/json-value";
import type { RelationCreateConfig } from "../relation/RelationPicker";
import { parseFormSpec, parseFormSpecPayload, type FormSpecWire, type FormSpecFieldType } from "./form-spec-schema";
export type { FormSpecFieldType } from "./form-spec-schema";

export interface DecisionFormActionOption {
  value: string;
  label: string;
  verdict: "COMPLETE" | "REJECT" | "ESCALATE";
  variant?: "primary" | "secondary" | "destructive" | "ghost";
  confirm?: string;
}

export interface DecisionFormValidation {
  valid: boolean;
  messages: Readonly<Record<string, readonly string[]>>;
}

/** One frozen native Decision schema, with Ajv owning every branch constraint. */
export interface DecisionActionFormSpec {
  readonly options: readonly DecisionFormActionOption[];
  readonly inputFields: readonly FormSpecFieldDescriptor[];
  readonly contextFields: readonly FormSpecFieldDescriptor[];
  fieldsFor(action: string): readonly FormSpecFieldDescriptor[];
  project(action: string, values: Readonly<Record<string, unknown>>): Record<string, unknown>;
  validate(candidate: unknown): DecisionFormValidation;
  validateContext(payload: unknown): DecisionFormValidation;
}

const DECISION_CONTEXT_WIDGETS = new Set(["record", "facts", "differences", "reasons"]);
const FORM_ANNOTATIONS = [
  "widget", "label", "placeholder", "layout", "defaultValue",
  "omittable", "presenceRequired", "options", "relation",
] as const;

/** Compile the original Draft 2020-12 schema; presentation parsing never strips validation rules. */
export function compileDecisionActionFormSpec(value: unknown, widgets: WidgetMap): DecisionActionFormSpec {
  const presented = parseFormSpec(value);
  const checkedJson = v.safeParse(JsonValueSchema, value);
  if (!checkedJson.success || checkedJson.output === null || Array.isArray(checkedJson.output)
      || typeof checkedJson.output !== "object") {
    throw new Error("Decision schema must be a JSON object.");
  }
  const action = presented.properties?.action;
  const values = action?.enum;
  const options = action?.options;
  if (action?.type !== "string" || !presented.required?.includes("action")
      || !values?.length || !options || options.length !== values.length
      || new Set(values).size !== values.length) {
    throw new Error("Decision schema needs one required action enum and matching options.");
  }
  const byValue = new Map<string, DecisionFormActionOption>();
  for (const option of options) {
    if (!option.verdict || !values.includes(option.value) || byValue.has(option.value)) {
      throw new Error("Decision action options must name a unique enum value and native verdict.");
    }
    byValue.set(option.value, option as DecisionFormActionOption);
  }
  const branches = presented.oneOf;
  if (!branches || branches.length !== values.length) {
    throw new Error("Decision schema needs one closed oneOf branch per action.");
  }
  const contextNames = new Set(Object.entries(presented.properties ?? {})
    .filter(([, field]) => field.layout === "context")
    .map(([name, field]) => {
      if (!DECISION_CONTEXT_WIDGETS.has(field.widget ?? "")) {
        throw new Error(`Decision context ${name} needs a typed shared widget.`);
      }
      return name;
    }));
  const byBranch = new Map<string, Set<string>>();
  for (const branch of branches) {
    const selected = branch.properties?.action?.const;
    const names = new Set(Object.keys(branch.properties ?? {}));
    if (branch.type !== "object" || branch.additionalProperties !== false
        || !Array.isArray(branch.required) || !branch.required.includes("action")
        || typeof selected !== "string" || !values.includes(selected)
        || byBranch.has(selected) || !names.has("action")
        || [...names].some((name) => !Object.hasOwn(presented.properties ?? {}, name) || contextNames.has(name))
        || branch.required.some((name) => !names.has(name))) {
      throw new Error("Decision branches need distinct closed action input scopes.");
    }
    byBranch.set(selected, names);
  }
  if (byBranch.size !== values.length || contextNames.has("action")
      || [...contextNames].some((name) => presented.required?.includes(name))) {
    throw new Error("Decision action/context scopes do not cover the declared schema.");
  }

  const ajv = new Ajv2020({
    allErrors: true, strict: true, coerceTypes: false, useDefaults: false, removeAdditional: false,
  });
  addFormats(ajv, { mode: "full" });
  for (const keyword of FORM_ANNOTATIONS) {
    if (!ajv.getKeyword(keyword)) ajv.addKeyword(keyword);
  }
  let validate: ValidateFunction;
  try {
    validate = ajv.compile(checkedJson.output);
  } catch (cause) {
    throw new Error(`Invalid Decision schema: ${String(cause)}`);
  }
  const fields = deserializeObjectFields(presented, widgets, "form spec");
  const contextFields = fields.filter((field) => contextNames.has(field.name));
  const inputFields = fields.filter((field) => field.name !== "action" && !contextNames.has(field.name));
  const contextValidators = Object.fromEntries([...contextNames].map((name) => {
    const root = checkedJson.output as Record<string, unknown>;
    const raw = root.properties;
    const properties = raw && typeof raw === "object" && !Array.isArray(raw)
      ? raw as Record<string, unknown> : {};
    const fieldSchema = properties[name];
    if (!fieldSchema || typeof fieldSchema !== "object" || Array.isArray(fieldSchema)) {
      throw new Error(`Decision context ${name} needs a JSON schema object.`);
    }
    return [name, ajv.compile({ ...fieldSchema, ...(root.$defs ? { $defs: root.$defs } : {}) })];
  })) as Record<string, ValidateFunction>;

  return {
    options: values.map((selected) => byValue.get(selected)!), inputFields, contextFields,
    fieldsFor(selected) {
      const names = byBranch.get(selected);
      if (!names) throw new Error("Choose a declared Decision action.");
      return inputFields.filter((field) => names.has(field.name));
    },
    project(selected, current) {
      return { ...normalizeFormSpecValues(this.fieldsFor(selected), current), action: selected };
    },
    validate(candidate) {
      return { valid: Boolean(validate(candidate)), messages: ajvErrorMessages(validate.errors) };
    },
    validateContext(payload) {
      const seeds = parseFormSpecPayload(payload);
      const messages: Record<string, string[]> = {};
      for (const name of contextNames) {
        if (!Object.hasOwn(seeds, name)) {
          messages[name] = ["Frozen Decision context is missing."];
          continue;
        }
        const checker = contextValidators[name];
        if (!checker || !checker(seeds[name])) messages[name] = ["Frozen Decision context is invalid."];
      }
      return { valid: Object.keys(messages).length === 0, messages };
    },
  };
}

function ajvErrorMessages(errors: readonly ErrorObject[] | null | undefined): Record<string, string[]> {
  const messages: Record<string, string[]> = {};
  for (const error of errors ?? []) {
    const missing = error.keyword === "required" && typeof error.params.missingProperty === "string"
      ? error.params.missingProperty : "";
    const pointer = error.instancePath.replace(/^\//, "").replace(/\//g, ".")
      .replace(/~1/g, "/").replace(/~0/g, "~");
    const name = pointer || missing || "root";
    (messages[name] ??= []).push(error.message ?? "Value does not satisfy this Decision action.");
  }
  return messages;
}

export type FormSpecRelationCreate = Pick<RelationCreateConfig, "resource" | "defaultValues">;

/**
 * Descriptor produced from a backend-emitted JSON form schema.
 * `type`/`properties`/`required`/`items`/`enum`/`const` are the recursive schema
 * vocabulary. Presentation extensions live on each property: string-only
 * `widget`/`label`/`description`/`placeholder`, `readOnly`, JSON `defaultValue`
 * (overriding the standard schema `default` when both are supplied),
 * string-labelled `options`, and the pure-data `relation` config. A property's
 * key becomes the descriptor's `name`; no function-valued extension is admitted.
 * Arrays of objects resolve to the registered fixed-N `rows` view composer.
 */
export interface FormSpecFieldDescriptor extends MutationDialogField {
  /** Approval layout intent; ordinary forms and unspecified fields remain inputs. */
  layout?: "context" | "input";
  rowTemplate?: readonly FormSpecFieldDescriptor[];
  objectTemplate?: readonly FormSpecFieldDescriptor[];
  itemTemplate?: FormSpecFieldDescriptor;
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
      const payloadValue = payloadValues[field.name];
      if (isFormSpecValueCompatible(field, payloadValue)) {
        values[field.name] = field.objectTemplate && payloadValue && typeof payloadValue === "object" && !Array.isArray(payloadValue)
          ? formSpecInitialValues(field.objectTemplate, payloadValue)
          : field.itemTemplate?.objectTemplate && Array.isArray(payloadValue)
            ? payloadValue.map((item) => item && typeof item === "object" && !Array.isArray(item)
              ? formSpecInitialValues(field.itemTemplate!.objectTemplate!, item)
              : item)
            : payloadValue;
        continue;
      }
    }
    if (field.hasDefault) {
      values[field.name] = field.defaultValue;
      continue;
    }
    if (!field.presenceRequired && (field.required || !field.omittable)) {
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
  const type = formSpecFieldType(field.type);
  const nullable = field.nullable || (Array.isArray(field.type) && field.type.includes("null"));
  const variableList = type === "array" && field.widget === "list";
  const rowTemplate = type === "array" && field.items && formSpecFieldType(field.items.type) === "object"
    && field.layout !== "context" && !variableList
    ? deserializeObjectFields(field.items, widgets, path)
    : undefined;
  const objectTemplate = type === "object" && field.widget === "object"
    ? deserializeObjectFields(field, widgets, path)
    : undefined;
  const itemTemplate = variableList && field.items
    ? deserializeField("item", field.items, true, widgets, `${path}[]`)
    : undefined;
  const { relation, widget: authoredWidget, label, description, placeholder, readOnly, layout } = field;
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
    ...(layout ? { layout } : {}),
    ...(Object.hasOwn(field, "defaultValue") ? { defaultValue: field.defaultValue, hasDefault: true }
      : Object.hasOwn(field, "default") ? { defaultValue: field.default, hasDefault: true } : {}),
    ...(options ? { options } : {}),
    ...(relation ? { relation } : {}),
    ...(rowTemplate ? { rowTemplate } : {}),
    ...(objectTemplate ? { objectTemplate } : {}),
    ...(itemTemplate ? { itemTemplate } : {}),
  };
}

function formSpecFieldType(type: FormSpecWire["type"]): FormSpecFieldType {
  if (Array.isArray(type)) {
    const nonNull = type.filter((value) => value !== "null");
    return nonNull.length === 1 ? nonNull[0] as FormSpecFieldType : "any";
  }
  return type ?? "any";
}

function optionsFrom(field: FormSpecWire): readonly WidgetOption[] | undefined {
  if (field.options) {
    return field.options.map(({ value, label, disabled }) => ({
      value, label, ...(disabled ? { disabled: true } : {}),
    }));
  }
  return field.enum?.map((value) => ({ value, label: value }));
}
