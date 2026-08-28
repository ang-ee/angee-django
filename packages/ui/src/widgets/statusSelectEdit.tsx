import type { ReactElement } from "react";

import { Select } from "../ui/select";
import { widgetLabel } from "./label";
import type { WidgetRenderProps } from "./types";

export function StatusSelectEdit({
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
      aria-label={widgetLabel(field, "Status")}
      placeholder={widgetLabel(field, "Status")}
      onValueChange={(next) => onChange?.(next)}
    />
  );
}
