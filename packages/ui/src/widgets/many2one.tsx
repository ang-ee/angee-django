import { useMemo, type ReactElement } from "react";

import { RelationField, type RelationOption } from "./RelationField";
import { widgetLabel } from "./label";
import { optionLabel, type WidgetDefinition, type WidgetRenderProps } from "./types";

function Many2OneEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  const options = useMemo<RelationOption[]>(
    () =>
      (field?.options ?? []).map((option) => ({
        value: option.value,
        label: typeof option.label === "string" ? option.label : option.value,
      })),
    [field?.options],
  );
  return (
    <RelationField
      value={value ?? ""}
      onChange={(next) => onChange?.(next)}
      options={options}
      readOnly={readOnly}
      aria-label={widgetLabel(field, "Related record")}
    />
  );
}

function Many2OneRead({
  value,
  field,
}: WidgetRenderProps<string>): ReactElement {
  const label = optionLabel(field?.options, value);
  return <span className="text-13 text-fg">{label}</span>;
}

export const many2oneWidget = {
  edit: Many2OneEdit,
  read: Many2OneRead,
  cell: Many2OneRead,
} satisfies WidgetDefinition<string>;
