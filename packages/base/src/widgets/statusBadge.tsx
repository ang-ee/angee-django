import type { ReactElement } from "react";

import { stateToneFromValue } from "../lib/tones";
import { Badge } from "../ui/badge";
import { Select } from "../ui/select";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

function StatusBadgeEdit({
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

function StatusBadgeRead({
  value,
  field,
}: WidgetRenderProps<string>): ReactElement {
  const label =
    field?.options?.find((option) => option.value === value)?.label ??
    value ??
    "";
  return (
    <Badge
      tone={stateToneFromValue(value ?? undefined)}
      density="compact"
      shape="pill"
    >
      {label}
    </Badge>
  );
}

export const statusBadgeWidget = {
  edit: StatusBadgeEdit,
  read: StatusBadgeRead,
  cell: StatusBadgeRead,
} satisfies WidgetDefinition<string>;
