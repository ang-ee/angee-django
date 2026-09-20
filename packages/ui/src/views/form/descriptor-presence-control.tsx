import type { ReactElement, ReactNode } from "react";
import type { WidgetFocusTarget } from "../../widgets";

import { useUiT } from "../../i18n";
import { Button } from "../../ui/button";
import type { FieldDescriptor } from "../page";
import { initialFormSpecValue, type FormSpecFieldDescriptor } from "./form-spec";

/** Shared omission/null affordances around either form owner's native control. */
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
  const compactScalar = isCompactOptionalScalar(field);
  if (compactScalar && (value === undefined || value === null)) {
    return <>{children}{!readOnly ? <div className="mt-1 flex flex-wrap items-center gap-1 text-xs text-fg-3">
      <span>{value === null ? t("form.value.leftEmpty") : t("form.value.optional")}</span>
      {field.nullable && value !== null ? <Button type="button" size="sm" variant="ghost"
        onClick={() => { onChange(null); onCommit?.(); }}>{t("form.value.leaveEmpty")}</Button> : null}
      {field.omittable && value !== undefined ? <Button type="button" size="sm" variant="ghost"
        onClick={() => { onChange(undefined); onCommit?.(); }}>{t("form.value.omit")}</Button> : null}
    </div> : null}</>;
  }
  if ((field.omittable || field.nullable) && value === undefined) {
    return <div className="flex items-center gap-2">
      <span className="text-13 text-fg-3">{t("form.value.notSet")}</span>
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
      <span className="text-13 text-fg-3">{t("form.value.leftEmpty")}</span>
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

function presentValue(field: FieldDescriptor, useDefault: boolean): unknown {
  if (!useDefault && isNumericInput(field)) {
    return "";
  }
  return initialFormSpecValue({
    ...field,
    nullable: useDefault ? field.nullable : false,
    hasDefault: useDefault,
  } as FormSpecFieldDescriptor);
}

/** Numeric widgets also render exact Decimal strings from JSON Schema unions. */
function isNumericInput(field: FieldDescriptor): boolean {
  return field.kind === "integer" || field.kind === "number"
    || field.widget === "integer" || field.widget === "float";
}
