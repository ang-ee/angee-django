import type { ReactElement } from "react";

import { useBaseT } from "../i18n";
import { Select } from "../ui/select";
import { widgetLabel } from "./label";
import { optionLabel, type WidgetDefinition, type WidgetRenderProps } from "./types";

function SelectEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  const t = useBaseT();
  const label = widgetLabel(field, t("select.label"));
  return (
    <Select
      value={value ?? ""}
      options={field?.options ?? []}
      readOnly={readOnly}
      disabled={readOnly}
      aria-label={label}
      placeholder={label}
      onValueChange={(next) => onChange?.(next)}
    />
  );
}

function SelectRead({
  value,
  field,
}: WidgetRenderProps<string>): ReactElement {
  const label = optionLabel(field?.options, value);
  return <span className="text-13 text-fg">{label}</span>;
}

export const selectWidget = {
  edit: SelectEdit,
  read: SelectRead,
  cell: SelectRead,
} satisfies WidgetDefinition<string>;

export const selectionWidget = {
  edit: SelectEdit,
  read: SelectRead,
  cell: SelectRead,
} satisfies WidgetDefinition<string>;
