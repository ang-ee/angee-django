import Ajv2020 from "ajv/dist/2020.js";
import type { ErrorObject, ValidateFunction } from "ajv";
import addFormats from "ajv-formats";
import * as v from "valibot";

import {
  FORM_SPEC_ANNOTATIONS,
  JsonValueSchema,
  deserializeFormSpec,
  normalizeFormSpecValues,
  parseFormSpec,
  parseFormSpecPayload,
  type FormSpecFieldDescriptor,
  type UiTranslate,
  type WidgetMap,
} from "@angee/ui";

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

const DECISION_CONTEXT_WIDGETS = new Set(["record", "facts", "differences", "reasons", "object"]);
const DecisionActionOptionSchema = v.strictObject({
  value: v.pipe(v.string(), v.minLength(1)),
  label: v.pipe(v.string(), v.minLength(1)),
  verdict: v.picklist(["COMPLETE", "REJECT", "ESCALATE"]),
  variant: v.optional(v.picklist(["primary", "secondary", "destructive", "ghost"])),
  confirm: v.optional(v.string()),
});

/** Compile the original Draft 2020-12 schema; presentation parsing never strips validation rules. */
export function compileDecisionActionFormSpec(
  value: unknown,
  widgets: WidgetMap,
  t: UiTranslate,
  locale: string = "en",
): DecisionActionFormSpec {
  const checkedJson = v.safeParse(JsonValueSchema, value);
  if (!checkedJson.success || checkedJson.output === null || Array.isArray(checkedJson.output)
      || typeof checkedJson.output !== "object") {
    throw new Error(t("inbox.validation.schemaObject"));
  }
  let presented: ReturnType<typeof parseFormSpec>;
  try {
    presented = parseFormSpec(value);
  } catch {
    throw new Error(t("inbox.validation.invalidSchema"));
  }
  const action = presented.properties?.action;
  const values = action?.enum;
  const options = action?.options;
  if (action?.type !== "string" || !presented.required?.includes("action")
      || !values?.length || !options || options.length !== values.length
      || new Set(values).size !== values.length) {
    throw new Error(t("inbox.validation.actionOptions"));
  }
  const byValue = new Map<string, DecisionFormActionOption>();
  for (const value of options) {
    const parsed = v.safeParse(DecisionActionOptionSchema, value);
    if (!parsed.success || !values.includes(parsed.output.value) || byValue.has(parsed.output.value)) {
      throw new Error(t("inbox.validation.uniqueActions"));
    }
    byValue.set(parsed.output.value, parsed.output);
  }
  const branches = presented.oneOf;
  if (!branches || branches.length !== values.length) {
    throw new Error(t("inbox.validation.closedBranches"));
  }
  const contextNames = new Set(Object.entries(presented.properties ?? {})
    .filter(([, field]) => field.layout === "context")
    .map(([name, field]) => {
      if (!DECISION_CONTEXT_WIDGETS.has(field.widget ?? "")) {
        throw new Error(t("inbox.validation.contextWidget", { name }));
      }
      return name;
    }));
  const byBranch = new Map<string, Set<string>>();
  const branchIndex = new Map<string, number>();
  for (const [index, branch] of branches.entries()) {
    const selected = branch.properties?.action?.const;
    const names = new Set(Object.keys(branch.properties ?? {}));
    if (branch.type !== "object" || branch.additionalProperties !== false
        || !Array.isArray(branch.required) || !branch.required.includes("action")
        || typeof selected !== "string" || !values.includes(selected)
        || byBranch.has(selected) || !names.has("action")
        || [...names].some((name) => !Object.hasOwn(presented.properties ?? {}, name) || contextNames.has(name))
        || branch.required.some((name) => !names.has(name))) {
      throw new Error(t("inbox.validation.distinctBranches"));
    }
    byBranch.set(selected, names);
    branchIndex.set(selected, index);
  }
  if (byBranch.size !== values.length || contextNames.has("action")
      || [...contextNames].some((name) => presented.required?.includes(name))) {
    throw new Error(t("inbox.validation.scopeCoverage"));
  }

  const ajv = new Ajv2020({
    allErrors: true, strict: true, coerceTypes: false, useDefaults: false, removeAdditional: false,
  });
  addFormats(ajv, { mode: "full" });
  for (const keyword of FORM_SPEC_ANNOTATIONS) {
    if (!ajv.getKeyword(keyword)) ajv.addKeyword(keyword);
  }
  let validate: ValidateFunction;
  try {
    validate = ajv.compile(checkedJson.output);
  } catch {
    throw new Error(t("inbox.validation.invalidSchema"));
  }
  let fields: readonly FormSpecFieldDescriptor[];
  try {
    fields = deserializeFormSpec(value, widgets);
  } catch {
    throw new Error(t("inbox.validation.invalidSchema"));
  }
  const contextFields = fields.filter((field) => contextNames.has(field.name));
  const inputFields = fields.filter((field) => field.name !== "action" && !contextNames.has(field.name));
  const contextValidators = Object.fromEntries([...contextNames].map((name) => {
    const root = checkedJson.output as Record<string, unknown>;
    const raw = root.properties;
    const properties = raw && typeof raw === "object" && !Array.isArray(raw)
      ? raw as Record<string, unknown> : {};
    const fieldSchema = properties[name];
    if (!fieldSchema || typeof fieldSchema !== "object" || Array.isArray(fieldSchema)) {
      throw new Error(t("inbox.validation.contextSchema", { name }));
    }
    try {
      return [name, ajv.compile({ ...fieldSchema, ...(root.$defs ? { $defs: root.$defs } : {}) })];
    } catch {
      throw new Error(t("inbox.validation.invalidSchema"));
    }
  })) as Record<string, ValidateFunction>;

  return {
    options: values.map((selected) => byValue.get(selected)!), inputFields, contextFields,
    fieldsFor(selected) {
      const names = byBranch.get(selected);
      if (!names) throw new Error(t("inbox.validation.chooseAction"));
      return inputFields.filter((field) => names.has(field.name));
    },
    project(selected, current) {
      return { ...normalizeFormSpecValues(this.fieldsFor(selected), current), action: selected };
    },
    validate(candidate) {
      const valid = Boolean(validate(candidate));
      const selected = candidate && typeof candidate === "object" && !Array.isArray(candidate)
        ? (candidate as Record<string, unknown>).action : undefined;
      return {
        valid,
        messages: ajvErrorMessages(
          validate.errors,
          candidate,
          typeof selected === "string" ? branchIndex.get(selected) : undefined,
          inputFields,
          t,
          locale,
        ),
      };
    },
    validateContext(payload) {
      const seeds = parseFormSpecPayload(payload);
      const messages: Record<string, string[]> = {};
      for (const name of contextNames) {
        if (!Object.hasOwn(seeds, name)) {
          messages[name] = [t("inbox.validation.contextMissing")];
          continue;
        }
        const checker = contextValidators[name];
        if (!checker || !checker(seeds[name])) messages[name] = [t("inbox.validation.contextInvalid")];
      }
      return { valid: Object.keys(messages).length === 0, messages };
    },
  };
}

function ajvErrorMessages(
  errors: readonly ErrorObject[] | null | undefined,
  candidate: unknown,
  selectedBranch: number | undefined,
  fields: readonly FormSpecFieldDescriptor[],
  t: UiTranslate,
  locale: string,
): Record<string, string[]> {
  const messages: Record<string, string[]> = {};
  const labels = new Map<string, string>(fields.map((field) => [
    field.name,
    typeof field.label === "string" ? field.label : field.name,
  ]));
  const relevant = (errors ?? []).filter((error) => {
    if (error.keyword === "oneOf" && error.schemaPath === "#/oneOf") return false;
    const branch = /^#\/oneOf\/(\d+)(?:\/|$)/.exec(error.schemaPath);
    return !branch || selectedBranch === undefined || Number(branch[1]) === selectedBranch;
  });
  const alternatives = relevant.filter((error) => error.keyword === "anyOf");
  const discarded = new Set<ErrorObject>();
  const synthetic: Array<{ name: string; message: string }> = [];
  for (const alternative of alternatives) {
    const prefix = `${alternative.schemaPath}/`;
    const children = relevant.filter((error) => error !== alternative && error.schemaPath.startsWith(prefix));
    const active = children.filter((error) => {
      const pointer = error.keyword === "required" && typeof error.params.missingProperty === "string"
        ? `${error.instancePath}/${escapeJsonPointer(error.params.missingProperty)}`
        : error.instancePath;
      return hasMeaningfulValue(candidate, pointer);
    });
    discarded.add(alternative);
    for (const child of children) {
      if (!active.includes(child)) discarded.add(child);
    }
    if (active.length === 0) {
      const names = [...new Set(children.map((error) => error.keyword === "required"
        && typeof error.params.missingProperty === "string"
        ? error.params.missingProperty : jsonPointerSegments(error.instancePath).at(-1) ?? "")
        .filter(Boolean))];
      const alternativesLabel = new Intl.ListFormat(locale, { style: "long", type: "disjunction" })
        .format(names.map((name) => labels.get(name) ?? name));
      synthetic.push({
        name: jsonPointerName(alternative.instancePath) || "root",
        message: alternativesLabel
          ? t("inbox.validation.completeAlternatives", { fields: alternativesLabel })
          : t("inbox.validation.completeAvailable"),
      });
    }
  }
  const seen = new Set<string>();
  for (const error of relevant) {
    if (discarded.has(error)) continue;
    const missing = error.keyword === "required" && typeof error.params.missingProperty === "string"
      ? error.params.missingProperty : "";
    const path = jsonPointerName(error.instancePath);
    const fullPath = [path, missing].filter(Boolean).join(".");
    const name = fullPath.split(".")[0] || "root";
    const leaf = fullPath.split(".").at(-1) || name;
    const label = labels.get(leaf) ?? leaf;
    const detail = decisionValidationMessage(error, label, t);
    const message = fullPath && fullPath !== name
      ? t("inbox.validation.detail", { label: fullPath, detail })
      : detail;
    const key = `${name}\u0000${message}`;
    if (!seen.has(key)) {
      seen.add(key);
      (messages[name] ??= []).push(message);
    }
  }
  for (const entry of synthetic) {
    const key = `${entry.name}\u0000${entry.message}`;
    if (!seen.has(key)) {
      seen.add(key);
      (messages[entry.name] ??= []).push(entry.message);
    }
  }
  return messages;
}

function decisionValidationMessage(error: ErrorObject, label: string, t: UiTranslate): string {
  if (error.keyword === "required") return t("inbox.validation.required", { label });
  if (error.keyword === "minLength" && typeof error.params.limit === "number") {
    const count = error.params.limit;
    return t("inbox.validation.minLength", { label, count });
  }
  if (error.keyword === "maxLength" && typeof error.params.limit === "number") {
    const count = error.params.limit;
    return t("inbox.validation.maxLength", { label, count });
  }
  if (error.keyword === "format") return t("inbox.validation.invalidFormat", { label });
  if (error.keyword === "pattern" || error.keyword === "type" || error.keyword === "enum"
      || error.keyword === "const") return t("inbox.validation.invalidValue", { label });
  return t("inbox.validation.invalidValue", { label });
}

function jsonPointerSegments(pointer: string): string[] {
  return pointer.split("/").slice(1).map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
}

function jsonPointerName(pointer: string): string {
  return jsonPointerSegments(pointer).join(".");
}

function escapeJsonPointer(value: string): string {
  return value.replace(/~/g, "~0").replace(/\//g, "~1");
}

function hasMeaningfulValue(value: unknown, pointer: string): boolean {
  let current = value;
  for (const part of jsonPointerSegments(pointer)) {
    if (!current || typeof current !== "object" || Array.isArray(current) || !Object.hasOwn(current, part)) {
      return false;
    }
    current = (current as Record<string, unknown>)[part];
  }
  return current !== null && current !== undefined && current !== "";
}
