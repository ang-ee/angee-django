import type { ReactElement } from "react";

import { useUiT } from "../i18n";
import { Select } from "../ui/select";
import { widgetLabel } from "./label";
import {
  canonicalOptionValue,
  optionLabel,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "./types";

function SelectEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  const t = useUiT();
  const label = widgetLabel(field, t("select.label"));
  const selected = canonicalOptionValue(field?.options, value) ?? value ?? "";
  return (
    <Select
      value={selected}
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
  const selected = canonicalOptionValue(field?.options, value) ?? value;
  const label = optionLabel(field?.options, selected);
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
