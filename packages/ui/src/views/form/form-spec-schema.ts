import type { ErrorObject } from "ajv";
import checkedFormSpec from "./form-spec.validator.generated.js";
import type { FormSpecWire } from "./form-spec.generated";

export type { FormSpecWire } from "./form-spec.generated";
export { FORM_SPEC_ANNOTATIONS } from "./form-spec.annotations.generated";
export type FormSpecFieldType = Exclude<Extract<FormSpecWire["type"], string>, "null">;

/** Validate the canonical emitted vocabulary before resolving widgets.
 * @kind null
 * @reason Codec infrastructure: validates the generated wire contract.
 */
export function parseFormSpec(value: unknown): FormSpecWire {
  if (checkedFormSpec(value)) return value;
  const errors = checkedFormSpec.errors ?? [];
  // Native branches report at their own locations; show the most specific issue.
  const issue = [...errors].sort((left, right) => right.instancePath.length - left.instancePath.length)[0];
  throw new Error(formSpecIssue(issue));
}

function formSpecIssue(issue: ErrorObject | undefined): string {
  if (!issue) return "Invalid form spec.";
  const segments = issue.instancePath.split("/").slice(1).map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
  if (issue.keyword === "required") segments.push(String(issue.params.missingProperty));
  const path = segments.filter((part) => part !== "properties").join(".") || "form spec";
  const message = issue.keyword === "enum" && segments.at(-1) === "operator"
    ? `unknown Refine CRUD operator "${String(issue.data)}".` : issue.message ?? "Invalid value.";
  return `Invalid ${path}: ${message}`;
}

/** Non-object payloads carry no seeds; declared fields own interpretation.
 * @kind null
 * @reason Codec infrastructure: reads a JSON object envelope.
 */
export function parseFormSpecPayload(payload: unknown): Record<string, unknown> {
  return payload !== null && typeof payload === "object" && !Array.isArray(payload) ? Object.fromEntries(Object.entries(payload)) : {};
}
