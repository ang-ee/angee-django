import type { ReactNode } from "react";

import { titleCase } from "./titleCase";
import { modelLabelSegment, type ModelEnumValueMetadata, type ModelMetadata } from "@angee/metadata";

/**
 * Humanize a bare enum/state member name for display (`IN_REVIEW` -> `In Review`).
 * @kind null
 * @reason Format utility: converts data to display or storage text.
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

/** A label named by an addon's own message key, resolved through the runtime i18n. */
export interface LabelKey {
  namespace: string;
  key: string;
  /** English from the addon's own message bundle for provider-less previews. */
  fallback?: string;
}

/** Whether a declared label is a message key rather than rendered content.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function isLabelKey(label: unknown): label is LabelKey {
  return Boolean(label) && typeof label === "object" && !Array.isArray(label)
    && "namespace" in (label as object) && "key" in (label as object);
}

export interface LabelKeyResolver {
  getFixedT: (language: null, namespace: string) => (key: string, options?: { defaultValue?: string }) => unknown;
}

/** The rendered label: a message key translated through the runtime, anything else as declared.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function resolveLabelKey(label: ReactNode | LabelKey, i18n: LabelKeyResolver | null): ReactNode {
  if (!isLabelKey(label)) return label;
  const translated = i18n?.getFixedT(null, label.namespace)(label.key, { defaultValue: label.fallback ?? label.key });
  return translated == null ? label.fallback ?? label.key : String(translated);
}
