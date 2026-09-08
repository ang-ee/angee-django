import type { FieldDescriptor } from "../page";

/** Return the native empty value for one descriptor field. */
export function emptyValueForField(field: Pick<FieldDescriptor, "widget" | "kind">): unknown {
  if (field.kind === "array" || field.widget === "tagInput") return [];
  if (field.kind === "object") return {};
  if (field.kind === "boolean" || field.kind === "switch" || field.widget === "switch") return false;
  if (field.kind === "integer" || field.kind === "number" || field.kind === "any") {
    return field.widget === "select" ? "" : null;
  }
  return "";
}

/** Validate the explicit JSON-presence contract and its native constraints. */
export function invalidPresenceValue(
  field: FieldDescriptor,
  value: unknown,
  present: boolean,
): boolean {
  if (!present || value === undefined) return true;
  if (value === null) return !field.nullable;
  return invalidDescriptorConstraint(field, value);
}

function invalidDescriptorConstraint(field: FieldDescriptor, value: unknown): boolean {
  if (typeof value === "string") {
    return (field.minLength !== undefined && value.length < field.minLength)
      || (field.maxLength !== undefined && value.length > field.maxLength);
  }
  if (typeof value === "number") {
    return (field.minimum !== undefined && value < field.minimum)
      || (field.maximum !== undefined && value > field.maximum);
  }
  if (Array.isArray(value)) {
    return (field.minItems !== undefined && value.length < field.minItems)
      || (field.maxItems !== undefined && value.length > field.maxItems);
  }
  return false;
}

/** Return exact dotted paths violating a structured FormSpec declaration. */
export function structuredFieldErrorPaths(
  field: FieldDescriptor,
  value: unknown,
  present: boolean,
  path = field.name,
): readonly string[] {
  const errors: string[] = [];
  if ((field.presenceRequired || present) && invalidPresenceValue(field, value, present)) {
    errors.push(path);
  }
  if (value && typeof value === "object" && !Array.isArray(value) && field.objectTemplate) {
    const record = value as Record<string, unknown>;
    for (const child of field.objectTemplate) {
      errors.push(...structuredFieldErrorPaths(
        child,
        record[child.name],
        Object.hasOwn(record, child.name),
        `${path}.${child.name}`,
      ));
    }
  }
  if (Array.isArray(value) && field.itemTemplate) {
    value.forEach((item, index) => {
      errors.push(...structuredFieldErrorPaths(field.itemTemplate!, item, true, `${path}.${index}`));
    });
  }
  return errors;
}

/** Whether a descriptor opts into JSON presence/constraint validation. */
export function isStructuredPresenceField(field: FieldDescriptor): boolean {
  return Boolean(
    field.presenceRequired || field.omittable || field.objectTemplate || field.itemTemplate
    || field.minLength !== undefined || field.maxLength !== undefined
    || field.minimum !== undefined || field.maximum !== undefined
    || field.minItems !== undefined || field.maxItems !== undefined
  );
}
