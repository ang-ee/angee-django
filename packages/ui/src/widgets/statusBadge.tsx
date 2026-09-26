import type { ReactElement } from "react";

import { Badge } from "../ui/badge";
import { useStatusTone } from "./use-status-tone";
import { StatusSelectEdit } from "./statusSelectEdit";
import { optionLabel, type WidgetDefinition, type WidgetRenderProps } from "./types";

function StatusBadgeRead({
  value,
  field,
}: WidgetRenderProps<string>): ReactElement {
  const statusTone = useStatusTone();
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
