import type * as React from "react";

import { resolveLabelKey, type LabelKey, type LabelKeyResolver } from "../../lib/labels";
import type { FieldDescriptor } from "../page/Field";
import type { GroupDescriptor } from "../page/Group";

/** The descriptor with every message-key label (its own, its object and item templates) resolved through the runtime.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function resolveFieldLabels<TField extends FieldDescriptor>(fields: readonly TField[], i18n: LabelKeyResolver | null): readonly TField[] {
  return fields.map((field) => resolveFieldLabel(field, i18n));
}

function resolveFieldLabel<TField extends FieldDescriptor>(field: TField, i18n: LabelKeyResolver | null): TField {
  const label = resolveLabelKey(field.label as React.ReactNode | LabelKey, i18n);
  const objectTemplate = field.objectTemplate ? resolveFieldLabels(field.objectTemplate, i18n) : undefined;
  const itemTemplate = field.itemTemplate ? resolveFieldLabel(field.itemTemplate, i18n) : undefined;
  if (label === field.label && objectTemplate === undefined && itemTemplate === undefined) return field;
  return {
    ...field,
    label,
    ...(objectTemplate ? { objectTemplate } : {}),
    ...(itemTemplate ? { itemTemplate } : {}),
  };
}

/** Groups with their own and their fields' message-key labels resolved.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function resolveGroupLabels<TGroup extends GroupDescriptor>(groups: readonly TGroup[], i18n: LabelKeyResolver | null): readonly TGroup[] {
  return groups.map((group) => ({
    ...group,
    label: resolveLabelKey(group.label as React.ReactNode | LabelKey, i18n),
    fields: resolveFieldLabels(group.fields, i18n),
  }));
}
