import type { ReactElement } from "react";

import { useUiT } from "../i18n";
import { Input } from "../ui/input";
import { textRoleVariants } from "../ui/text";
import { formatTime, formatTimeInput, valueLabel } from "./date-format";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

/** A GraphQL `Time` wire value ("13:45", "13:45:00"), or empty. */
type TimeWidgetValue = string | null;

function TimeEdit({
  value,
  onChange,
  onCommit,
  field,
  readOnly,
  controlRef,
}: WidgetRenderProps<TimeWidgetValue>): ReactElement {
  const t = useUiT();
  if (readOnly) return <TimeRead value={value} />;
  return (
    <Input
      ref={controlRef}
      {...field?.controlProps}
      type="time"
      value={formatTimeInput(value)}
      aria-label={widgetLabel(field, t("time.label"))}
      className="tabular-nums"
      // The native control's `HH:mm` is the wire value; clearing it stores no time.
      onChange={(event) => onChange?.(event.currentTarget.value || null)}
      onBlur={onCommit}
    />
  );
}

function TimeRead({ value }: WidgetRenderProps<TimeWidgetValue>): ReactElement {
  const label = formatTime(value);
  return (
    <span className={textRoleVariants({ role: "value" })} title={valueLabel(value)}>
      {label || "—"}
    </span>
  );
}

function TimeCell({ value }: WidgetRenderProps<TimeWidgetValue>): ReactElement {
  return <span className={textRoleVariants({ numeric: true })}>{formatTime(value) || "—"}</span>;
}

export const timeWidget = {
  edit: TimeEdit,
  read: TimeRead,
  cell: TimeCell,
} satisfies WidgetDefinition<TimeWidgetValue>;
