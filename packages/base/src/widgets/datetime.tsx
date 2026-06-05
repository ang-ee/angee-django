import { useState, type ReactElement } from "react";
import { format, isValid, parseISO } from "date-fns";

import { Glyph } from "../chrome/Glyph";
import { Button } from "../ui/button";
import { Calendar } from "../ui/calendar";
import { Input } from "../ui/input";
import {
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  PopoverRoot,
  PopoverTrigger,
} from "../ui/popover";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

type DatetimeWidgetValue = string | Date | null;

function DatetimeEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<DatetimeWidgetValue>): ReactElement {
  const [open, setOpen] = useState(false);
  const date = dateFromValue(value);
  const label =
    formatDatetime(date) || widgetLabel(field, "Select date and time");

  if (readOnly) return <DatetimeRead value={value} />;

  return (
    <PopoverRoot open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className="inline-flex h-9 w-full min-w-0 items-center justify-between gap-2 rounded border border-border bg-inset px-2 text-left text-13 text-fg outline-none transition-colors hover:border-border-strong focus-visible:border-border-focus focus-visible:focus-ring"
        aria-label={widgetLabel(field, "Date and time")}
      >
        <span className="min-w-0 truncate">{label}</span>
        <Glyph name="calendar" className="shrink-0 text-fg-muted" />
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverPositioner sideOffset={4} align="start">
          <PopoverContent
            aria-label={widgetLabel(field, "Date and time")}
            surface="sheet"
          >
            <Calendar
              fixedWeeks
              mode="single"
              selected={date ?? undefined}
              showOutsideDays
              onSelect={(next) => {
                if (!next) return;
                const selected = new Date(next);
                if (date) {
                  selected.setHours(date.getHours(), date.getMinutes(), 0, 0);
                }
                onChange?.(formatStorage(selected));
              }}
            />
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
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}

function DatetimeRead({
  value,
}: WidgetRenderProps<DatetimeWidgetValue>): ReactElement {
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
} satisfies WidgetDefinition<DatetimeWidgetValue>;

function dateFromValue(value: DatetimeWidgetValue | undefined): Date | null {
  if (value instanceof Date) return isValid(value) ? value : null;
  if (!value) return null;
  const parsed = parseISO(value);
  return isValid(parsed) ? parsed : null;
}

function formatDatetime(value: Date | null): string {
  return value ? format(value, "MMM d, yyyy, p") : "";
}

function formatStorage(value: Date): string {
  return format(value, "yyyy-MM-dd'T'HH:mm");
}

function valueLabel(value: DatetimeWidgetValue | undefined): string {
  if (value instanceof Date) return value.toISOString();
  return value ?? "";
}
