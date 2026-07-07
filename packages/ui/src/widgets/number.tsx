import type { ReactElement } from "react";

import { NumberField } from "../ui/number-field";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

type NumericWidgetValue = number | string | null;

function IntegerEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<NumericWidgetValue>): ReactElement {
  return (
    <NumberField
      value={normaliseNumber(value)}
      readOnly={readOnly}
      step={1}
      snapOnStep
      inputProps={{
        "aria-label": widgetLabel(field, "Integer"),
        inputMode: "numeric",
      }}
      onValueChange={(next) =>
        onChange?.(next === null ? null : Math.trunc(next))
      }
    />
  );
}

function FloatEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<NumericWidgetValue>): ReactElement {
  return (
    <NumberField
      value={normaliseNumber(value)}
      readOnly={readOnly}
      step={0.01}
      inputProps={{
        "aria-label": widgetLabel(field, "Decimal number"),
        inputMode: "decimal",
      }}
      onValueChange={(next) => onChange?.(next)}
    />
  );
}

function NumberRead({
  value,
}: WidgetRenderProps<NumericWidgetValue>): ReactElement {
  return (
    <span className="text-13 tabular-nums text-fg">
      {formatNumber(value)}
    </span>
  );
}

export const integerWidget = {
  edit: IntegerEdit,
  read: NumberRead,
  cell: NumberRead,
} satisfies WidgetDefinition<NumericWidgetValue>;

export const floatWidget = {
  edit: FloatEdit,
  read: NumberRead,
  cell: NumberRead,
} satisfies WidgetDefinition<NumericWidgetValue>;

function normaliseNumber(value: NumericWidgetValue | undefined): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  // Decimal columns arrive as exact strings on the wire (the Decimal scalar);
  // a numeric widget still renders and edits them as numbers.
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatNumber(value: NumericWidgetValue | undefined): string {
  const number = normaliseNumber(value);
  return number === null ? "" : String(number);
}
