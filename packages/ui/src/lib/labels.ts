import { titleCase } from "./titleCase";
import { modelLabelSegment, type ModelEnumValueMetadata, type ModelMetadata } from "@angee/metadata";

/**
 * Humanize a bare enum/state member name for display (`IN_REVIEW` -> `In Review`).
 */
export function statusLabel(value: string): string {
  return titleCase(value.toLowerCase());
}

export function groupFieldLabel(field: string): string {
  const label = titleCase(field);
  return label.endsWith(" At") ? label.slice(0, -3) : label;
}

/** Use a model's scoped vocabulary label before humanizing its model name (`parties.Person` -> `Person`). */
export function modelDisplayLabel(model: Pick<ModelMetadata, "label"> | null | undefined, resource: string): string {
  return model?.label ?? titleCase(modelLabelSegment(resource));
}

/** Use the resource's authored label before humanizing an enum member. */
export function enumValueLabel(value: ModelEnumValueMetadata): string {
  return value.description ?? statusLabel(value.value);
}
