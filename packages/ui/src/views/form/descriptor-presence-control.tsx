import type { ReactElement, ReactNode } from "react";
import type { WidgetFocusTarget } from "../../widgets";

import { useUiT } from "../../i18n";
import { Button } from "../../ui/button";
import type { FieldDescriptor } from "../page";
import { NUMBER_WIDGETS, emptyValueForField } from "./field-values";
import { initialFormSpecValue, type FormSpecFieldDescriptor } from "./form-spec";

/**
 * Shared omission/null affordances around either form owner's native control. "Set value" writes the
 * field's empty value, or its default with "Use default"; a field holding nothing that shows its own
 * control (`showsOwnControl`) offers neither, so neither ever writes a value the field already holds.
 */
export function DescriptorPresenceControl({ field, value, readOnly, onChange, onCommit, controlRef, children }: {
  field: FieldDescriptor;
  value: unknown;
  readOnly?: boolean;
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  controlRef?: (target: WidgetFocusTarget | null) => void;
  children: ReactNode;
}): ReactElement {
  const t = useUiT();
  if (showsOwnControl(field, value)) {
    return <>{children}{!readOnly ? <div className="mt-1 flex flex-wrap items-center gap-1 text-12 text-fg-subtle">
      <span>{value === null ? t("form.value.leftEmpty") : field.omittable ? t("form.value.optional") : t("form.value.notSet")}</span>
      {field.nullable && value !== null ? <Button type="button" size="sm" variant="ghost"
        onClick={() => { onChange(null); onCommit?.(); }}>{t("form.value.leaveEmpty")}</Button> : null}
      {field.omittable && value !== undefined ? <Button type="button" size="sm" variant="ghost"
        onClick={() => { onChange(undefined); onCommit?.(); }}>{t("form.value.omit")}</Button> : null}
    </div> : null}</>;
  }
  if ((field.omittable || field.nullable) && value === undefined) {
    return <div className="flex items-center gap-2">
      <span className="text-13 text-fg-subtle">{t("form.value.notSet")}</span>
      {!readOnly ? <Button ref={controlRef} type="button" size="sm" variant="secondary"
        onClick={() => { onChange(presentValue(field, Boolean(field.hasDefault))); onCommit?.(); }}>
        {field.hasDefault ? t("form.value.useDefault") : t("form.value.set")}
      </Button> : null}
      {!readOnly && field.nullable ? <Button type="button" size="sm" variant="ghost"
        onClick={() => { onChange(null); onCommit?.(); }}>{t("form.value.leaveEmpty")}</Button> : null}
    </div>;
  }
  if (field.nullable && value === null) {
    return <>
      <span className="text-13 text-fg-subtle">{t("form.value.leftEmpty")}</span>
      {!readOnly ? <div className="mt-1 flex flex-wrap gap-1">
        <Button ref={controlRef} type="button" size="sm" variant="ghost"
          onClick={() => { onChange(presentValue(field, false)); onCommit?.(); }}>
          {t("form.value.set")}
        </Button>
        {field.omittable ? <Button type="button" size="sm" variant="ghost"
          onClick={() => { onChange(undefined); onCommit?.(); }}>{t("form.value.notSet")}</Button> : null}
      </div> : null}
    </>;
  }
  return <>{children}{!readOnly && (field.omittable || field.nullable) ? <div className="mt-1 flex flex-wrap gap-1">
    {field.nullable ? <Button type="button" size="sm" variant="ghost"
      onClick={() => { onChange(null); onCommit?.(); }}>{t("form.value.leaveEmpty")}</Button> : null}
    {field.omittable ? <Button type="button" size="sm" variant="ghost"
      onClick={() => { onChange(undefined); onCommit?.(); }}>{t("form.value.notSet")}</Button> : null}
  </div> : null}</>;
}

/**
 * Whether `field`, holding nothing (`undefined`) or `null`, shows its own control with a hint rather
 * than a placeholder: a plain optional input (`isCompactOptionalScalar`), or a field whose empty value
 * is `null` (`emptyValueForField`: a number, a date, an untyped value), which has no "present but
 * empty" state, holding `null`, or nothing with no default to use.
 */
function showsOwnControl(field: FieldDescriptor, value: unknown): boolean {
  if (value !== undefined && value !== null) return false;
  if (isCompactOptionalScalar(field)) return true;
  return Boolean(field.omittable || field.nullable) && emptyValueForField(field) === null
    && (value === null || !field.hasDefault);
}

/** Plain optional inputs stay usable while retaining distinct omitted/null values. */
function isCompactOptionalScalar(field: FieldDescriptor): boolean {
  const numericInput = isNumericInput(field);
  return Boolean(
    field.omittable
    && (
      !field.hasDefault
      || (field.defaultValue === null && numericInput)
    )
    && !field.presenceRequired
    && !field.objectTemplate
    && !field.itemTemplate
    && (field.kind === "string" || numericInput),
  );
}

/**
 * What "Use default" writes, the field's default, or "Set value": its empty value or its template's
 * seeds, never `null`, since a field whose empty value is `null` shows its own control instead.
 */
function presentValue(field: FieldDescriptor, useDefault: boolean): unknown {
  return initialFormSpecValue({
    ...field,
    nullable: useDefault ? field.nullable : false,
    hasDefault: useDefault,
  } as FormSpecFieldDescriptor);
}

/** A number by its kind or by its own widget (`NUMBER_WIDGETS`); numeric widgets also render exact Decimal strings from JSON Schema unions. */
function isNumericInput(field: FieldDescriptor): boolean {
  return field.kind === "integer" || field.kind === "number" || NUMBER_WIDGETS.has(field.widget ?? "");
}
