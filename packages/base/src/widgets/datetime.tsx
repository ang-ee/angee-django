import type { ReactElement } from "react";
import { format, isValid, parseISO } from "date-fns";

import type { WidgetDefinition, WidgetRenderProps } from "./types";

function DatetimeRead({
  value,
}: WidgetRenderProps<string | Date>): ReactElement {
  const date = dateFromValue(value);
  const label = date ? format(date, "MMM d, yyyy, p") : valueLabel(value);
  return (
    <span className="text-13 tabular-nums text-fg" title={valueLabel(value)}>
      {label}
    </span>
  );
}

export const datetimeWidget = {
  read: DatetimeRead,
  cell: DatetimeRead,
} satisfies WidgetDefinition<string | Date>;

function dateFromValue(value: string | Date | null | undefined): Date | null {
  if (value instanceof Date) return isValid(value) ? value : null;
  if (!value) return null;
  const parsed = parseISO(value);
  return isValid(parsed) ? parsed : null;
}

function valueLabel(value: string | Date | null | undefined): string {
  if (value instanceof Date) return value.toISOString();
  return value ?? "";
}
