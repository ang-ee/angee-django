import { useState, type ReactElement } from "react";

import { useUiT } from "../i18n";
import { Button } from "../ui/button";
import {
  DatePopover,
  dateFromValue,
  valueLabel,
  type DateWidgetValue,
} from "./date-popover";
import { formatDate, formatDateStorage } from "./date-format";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

function DateEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<DateWidgetValue>): ReactElement {
  const t = useUiT();
  const [open, setOpen] = useState(false);
  const date = dateFromValue(value);
  const label = formatDate(date) || widgetLabel(field, t("date.select"));

  if (readOnly) return <DateRead value={value} />;

  return (
    <DatePopover
      selected={date}
      label={label}
      ariaLabel={widgetLabel(field, t("date.label"))}
      open={open}
      onOpenChange={setOpen}
      onSelectDate={(next) => {
        onChange?.(formatDateStorage(next));
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
              {t("date.clear")}
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
