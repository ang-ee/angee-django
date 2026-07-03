import type { ReactElement } from "react";

import { Input } from "../ui/input";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

function TextEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Input
      value={value ?? ""}
      readOnly={readOnly}
      aria-label={widgetLabel(field, "Text")}
      placeholder={field?.placeholder ?? (typeof field?.label === "string" ? field.label : undefined)}
      onChange={(event) => onChange?.(event.currentTarget.value)}
    />
  );
}

function TextRead({
  value,
}: WidgetRenderProps<string>): ReactElement {
  return <span className="text-13 text-fg">{value ?? ""}</span>;
}

export const textWidget = {
  edit: TextEdit,
  read: TextRead,
  cell: TextRead,
} satisfies WidgetDefinition<string>;
