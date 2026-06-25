import type { ReactElement } from "react";

import { Badge } from "../ui/badge";
import { statusTone } from "./status-tones";
import { StatusSelectEdit } from "./statusSelectEdit";
import { optionLabel, type WidgetDefinition, type WidgetRenderProps } from "./types";

function StatusBadgeRead({
  value,
  field,
}: WidgetRenderProps<string>): ReactElement {
  const label = optionLabel(field?.options, value);
  return (
    <Badge
      tone={statusTone(value, field?.tone)}
      density="compact"
      shape="pill"
    >
      {label}
    </Badge>
  );
}

export const statusBadgeWidget = {
  edit: StatusSelectEdit,
  read: StatusBadgeRead,
  cell: StatusBadgeRead,
} satisfies WidgetDefinition<string>;
