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
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<DateWidgetValue>): ReactElement {
  const t = useUiT();
  const [open, setOpen] = useState(false);
  const date = dateFromValue(value);
  const label = formatDate(date, { density: "full" }) || widgetLabel(field, t("date.select"));

  if (readOnly) return <DateRead value={value} />;

  return (
    <DatePopover
      controlProps={field?.controlProps}
      triggerRef={controlRef}
      selected={date}
      label={label}
      ariaLabel={widgetLabel(field, t("date.label"))}
      open={open}
      onOpenChange={setOpen}
      onSelectDate={(next) => {
        onChange?.(formatDateStorage(next));
        onCommit?.();
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
                onCommit?.();
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
  const label = formatDate(date, { density: "full" });
  return (
    <span className="text-13 tabular-nums text-fg" title={label || valueLabel(value)}>
      {label || "—"}
    </span>
  );
}

function DateCell({ value }: WidgetRenderProps<DateWidgetValue>): ReactElement {
  const date = dateFromValue(value);
  return <span className="tabular-nums" title={formatDate(date, { density: "full" })}>
    {formatDate(date, { density: "list" }) || "—"}
  </span>;
}

export const dateWidget = {
  edit: DateEdit,
  read: DateRead,
  cell: DateCell,
} satisfies WidgetDefinition<DateWidgetValue>;
