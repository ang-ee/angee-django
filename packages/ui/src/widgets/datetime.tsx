import { useState, type ReactElement } from "react";

import { useBaseT } from "../i18n";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import {
  DatePopover,
  dateFromValue,
  valueLabel,
  type DateWidgetValue,
} from "./date-popover";
import {
  formatDateTime,
  formatDateTimeStorage,
  formatTimeInput,
} from "./date-format";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

function DatetimeEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<DateWidgetValue>): ReactElement {
  const t = useBaseT();
  const [open, setOpen] = useState(false);
  const date = dateFromValue(value);
  const label =
    formatDateTime(date) || widgetLabel(field, t("datetime.select"));

  if (readOnly) return <DatetimeRead value={value} />;

  return (
    <DatePopover
      selected={date}
      label={label}
      ariaLabel={widgetLabel(field, t("datetime.label"))}
      open={open}
      onOpenChange={setOpen}
      onSelectDate={(next) => {
        if (!next) return;
        const selected = new Date(next);
        if (date) {
          selected.setHours(date.getHours(), date.getMinutes(), 0, 0);
        }
        onChange?.(formatDateTimeStorage(selected));
      }}
      footer={
        <div className="flex items-center justify-between gap-2 border-t border-border-subtle p-2">
          <Input
            type="time"
            value={formatTimeInput(date)}
            disabled={!date}
            aria-label={t("datetime.time")}
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
              onChange?.(formatDateTimeStorage(next));
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
              {t("date.clear")}
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
  const label = formatDateTime(date);
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
