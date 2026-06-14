import { useState, type ReactElement } from "react";
import { format } from "date-fns";

import { Button } from "../ui/button";
import {
  DatePopover,
  dateFromValue,
  valueLabel,
  type DateWidgetValue,
} from "./date-popover";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

function DateEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<DateWidgetValue>): ReactElement {
  const [open, setOpen] = useState(false);
  const date = dateFromValue(value);
  const label = formatDate(date) || widgetLabel(field, "Select date");

  if (readOnly) return <DateRead value={value} />;

  return (
    <DatePopover
      selected={date}
      label={label}
      ariaLabel={widgetLabel(field, "Date")}
      open={open}
      onOpenChange={setOpen}
      onSelectDate={(next) => {
        onChange?.(next ? format(next, "yyyy-MM-dd") : null);
        setOpen(false);
      }}
      footer={
        date ? (
          <div className="border-t border-border-subtle p-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="w-full"
              onClick={() => {
                onChange?.(null);
                setOpen(false);
              }}
            >
              Clear
            </Button>
          </div>
        ) : null
      }
    />
  );
}

function DateRead({
  value,
}: WidgetRenderProps<DateWidgetValue>): ReactElement {
  const date = dateFromValue(value);
  const label = formatDate(date);
  return (
    <span className="text-13 tabular-nums text-fg" title={valueLabel(value)}>
      {label}
    </span>
  );
}

export const dateWidget = {
  edit: DateEdit,
  read: DateRead,
  cell: DateRead,
} satisfies WidgetDefinition<DateWidgetValue>;

function formatDate(value: Date | null): string {
  return value ? format(value, "MMM d, yyyy") : "";
}
