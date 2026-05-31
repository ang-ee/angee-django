import type { ReactElement } from "react";

import { Select } from "../ui/select";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

function SelectEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Select
      value={value ?? ""}
      options={field?.options ?? []}
      readOnly={readOnly}
      disabled={readOnly}
      aria-label={widgetLabel(field, "Select")}
      placeholder={widgetLabel(field, "Select")}
      onValueChange={(next) => onChange?.(next)}
    />
  );
}

function SelectRead({
  value,
  field,
}: WidgetRenderProps<string>): ReactElement {
  const label =
    field?.options?.find((option) => option.value === value)?.label ?? value ?? "";
  return <span className="text-13 text-fg">{label}</span>;
}

export const selectWidget = {
  edit: SelectEdit,
  read: SelectRead,
  cell: SelectRead,
} satisfies WidgetDefinition<string>;
