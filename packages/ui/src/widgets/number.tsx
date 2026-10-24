import { useEffect, type ReactElement } from "react";

import { Input } from "../ui/input";
import { formatNumber, isNumericString } from "../lib/format-number";
import { NumberField } from "../ui/number-field";
import { textRoleVariants } from "../ui/text";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

type NumericWidgetValue = number | string | null;

function IntegerEdit({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
  onValidityChange,
}: WidgetRenderProps<NumericWidgetValue>): ReactElement {
  if (field?.scalar === "BigInt") return <ExactNumberEdit value={value} onChange={onChange} onCommit={onCommit} field={field} readOnly={readOnly} controlRef={controlRef} onValidityChange={onValidityChange} />;
  return (
    <NumberField
      presentation={field?.controlProps?.presentation}
      className="w-full"
      value={normaliseNumber(value)}
      readOnly={readOnly}
      step={1}
      snapOnStep
      inputProps={{
        ref: controlRef,
        ...field?.controlProps,
        "aria-label": widgetLabel(field, "Integer"),
        inputMode: "numeric",
        onBlur: onCommit,
      }}
      onValueChange={(next, details) => onChange?.(
        details.reason === "input-clear"
          ? ""
          : next === null ? null : Math.trunc(next),
      )}
    />
  );
}

function FloatEdit({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
  onValidityChange,
}: WidgetRenderProps<NumericWidgetValue>): ReactElement {
  if (field?.scalar === "Decimal") return <ExactNumberEdit value={value} onChange={onChange} onCommit={onCommit} field={field} readOnly={readOnly} controlRef={controlRef} onValidityChange={onValidityChange} />;
  return (
    <NumberField
      presentation={field?.controlProps?.presentation}
      className="w-full"
      value={normaliseNumber(value)}
      readOnly={readOnly}
      step={0.01}
      inputProps={{
        ref: controlRef,
        ...field?.controlProps,
        "aria-label": widgetLabel(field, "Decimal number"),
        inputMode: "decimal",
        onBlur: onCommit,
      }}
      onValueChange={(next, details) => onChange?.(
        details.reason === "input-clear" ? "" : next,
      )}
    />
  );
}

/** NumberField accepts JS numbers only; exact wire scalars compose the native text input. */
function ExactNumberEdit({ value, onChange, onCommit, field, readOnly, controlRef, onValidityChange }: WidgetRenderProps<NumericWidgetValue>): ReactElement {
  const text = value == null ? "" : String(value);
  let valid = text.trim() === "";
  if (!valid && field?.scalar === "BigInt") {
    try { BigInt(text); valid = true; } catch { /* Incomplete input remains editable. */ }
  } else if (!valid) valid = isNumericString(text);
  useEffect(() => {
    onValidityChange?.(valid);
    return () => onValidityChange?.(true);
  }, [onValidityChange, valid]);
  return <Input {...field?.controlProps} ref={controlRef} type="text"
    inputMode={field?.scalar === "BigInt" ? "numeric" : "decimal"}
    value={text} readOnly={readOnly} aria-label={widgetLabel(field, field?.scalar === "BigInt" ? "Integer" : "Decimal number")}
    onChange={(event) => onChange?.(event.currentTarget.value)} onBlur={onCommit} />;
}

function NumberRead({
  value,
}: WidgetRenderProps<NumericWidgetValue>): ReactElement {
  return (
    <span className={textRoleVariants({ role: "value" })}>
      {formatNumber(value, { useGrouping: false, maximumFractionDigits: 20 })}
    </span>
  );
}

function NumberCell({ value }: WidgetRenderProps<NumericWidgetValue>): ReactElement {
  return <span className={textRoleVariants({ numeric: true })}>{formatNumber(value, { useGrouping: false, maximumFractionDigits: 20 }) || "—"}</span>;
}

function PercentRead({ value }: WidgetRenderProps<NumericWidgetValue>): ReactElement {
  const text = formatNumber(value, { style: "unit", unit: "percent", maximumFractionDigits: 2 });
  // An exact calculation can retain an unbounded percentage as a Decimal
  // infinity string. Keep that fact visible without changing numeric formatting's
  // general policy of omitting invalid or non-finite inputs.
  const infinity = value === Infinity || value === -Infinity || value === "Infinity" || value === "-Infinity";
  return <span className={textRoleVariants({ role: "value", numeric: true })}>{text || (infinity ? String(value) : "")}</span>;
}

export const integerWidget = {
  edit: IntegerEdit,
  read: NumberRead,
  cell: NumberCell,
} satisfies WidgetDefinition<NumericWidgetValue>;

export const floatWidget = {
  edit: FloatEdit,
  read: NumberRead,
  cell: NumberCell,
} satisfies WidgetDefinition<NumericWidgetValue>;

/** Read percentage points (65.687 means 65.69%), retaining the exact source value. */
export const percentWidget = {
  read: PercentRead,
  cell: PercentRead,
} satisfies WidgetDefinition<NumericWidgetValue>;

function normaliseNumber(value: NumericWidgetValue | undefined): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  // Only number-valued Int/Float controls use this adapter; exact scalars stay strings.
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}
