import { useState, type ReactElement } from "react";
import { format } from "date-fns";

import { Button } from "../ui/button";
import { Input } from "../ui/input";
import {
  DatePopover,
  dateFromValue,
  valueLabel,
  type DateWidgetValue,
} from "./date-popover";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

function DatetimeEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<DateWidgetValue>): ReactElement {
  const [open, setOpen] = useState(false);
  const date = dateFromValue(value);
  const label =
    formatDatetime(date) || widgetLabel(field, "Select date and time");

  if (readOnly) return <DatetimeRead value={value} />;

  return (
    <DatePopover
      selected={date}
      label={label}
      ariaLabel={widgetLabel(field, "Date and time")}
      open={open}
      onOpenChange={setOpen}
      onSelectDate={(next) => {
        if (!next) return;
        const selected = new Date(next);
        if (date) {
          selected.setHours(date.getHours(), date.getMinutes(), 0, 0);
        }
        onChange?.(formatStorage(selected));
      }}
      footer={
        <div className="flex items-center justify-between gap-2 border-t border-border-subtle p-2">
          <Input
            type="time"
            value={date ? format(date, "HH:mm") : ""}
            disabled={!date}
            aria-label="Time"
            className="h-8 tabular-nums"
            onChange={(event) => {
              if (!date) return;
              const [hours, minutes] = event.currentTarget.value
                .split(":")
                .map((part) => Number(part));
              const safeHours =
                typeof hours === "number" && Number.isFinite(hours)
                  ? hours
                  : 0;
              const safeMinutes =
                typeof minutes === "number" && Number.isFinite(minutes)
                  ? minutes
                  : 0;
              const next = new Date(date);
              next.setHours(safeHours, safeMinutes, 0, 0);
              onChange?.(formatStorage(next));
            }}
          />
          {date ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                onChange?.(null);
                setOpen(false);
              }}
            >
              Clear
            </Button>
          ) : null}
        </div>
      }
    />
  );
}

function DatetimeRead({
  value,
}: WidgetRenderProps<DateWidgetValue>): ReactElement {
  const date = dateFromValue(value);
  const label = formatDatetime(date) || valueLabel(value);
  return (
    <span className="text-13 tabular-nums text-fg" title={valueLabel(value)}>
      {label}
    </span>
  );
}

export const datetimeWidget = {
  edit: DatetimeEdit,
  read: DatetimeRead,
  cell: DatetimeRead,
} satisfies WidgetDefinition<DateWidgetValue>;

function formatDatetime(value: Date | null): string {
  return value ? format(value, "MMM d, yyyy, p") : "";
}

function formatStorage(value: Date): string {
  return format(value, "yyyy-MM-dd'T'HH:mm");
}
