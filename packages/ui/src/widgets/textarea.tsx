import type { ReactElement } from "react";

import { Textarea } from "../ui/textarea";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

function TextareaEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  return (
    <Textarea
      value={value ?? ""}
      readOnly={readOnly}
      aria-label={widgetLabel(field, "Text")}
      placeholder={field?.label === undefined ? undefined : widgetLabel(field, "Text")}
      rows={6}
      onChange={(event) => onChange?.(event.currentTarget.value)}
    />
  );
}

function TextareaRead({
  value,
}: WidgetRenderProps<string>): ReactElement {
  return <p className="whitespace-pre-wrap text-13 text-fg">{value ?? ""}</p>;
}

export const textareaWidget = {
  edit: TextareaEdit,
  read: TextareaRead,
  cell: TextareaRead,
} satisfies WidgetDefinition<string>;
