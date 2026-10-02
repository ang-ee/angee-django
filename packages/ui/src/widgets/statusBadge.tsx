import type { ReactElement } from "react";

import { Badge } from "../ui/badge";
import { StatusIcon } from "../ui/status-icon";
import { statusLabel } from "../lib/labels";
import { useStatusTone } from "./use-status-tone";
import { StatusSelectEdit } from "./statusSelectEdit";
import { canonicalOptionValue, optionLabel, type WidgetDefinition, type WidgetRenderProps } from "./types";

function StatusBadgeRead({
  value,
  field,
}: WidgetRenderProps<string>): ReactElement {
  const statusTone = useStatusTone();
  const label = canonicalOptionValue(field?.options, value)
    ? optionLabel(field?.options, value)
    : statusLabel(value ?? "");
  if (!value) return <span>—</span>;
  const tone = statusTone(value, field?.tone);
  const iconTone = tone === "info" || tone === "success" || tone === "warning" || tone === "danger"
    ? tone : "muted";
  return (
    <Badge
      tone={tone}
      density="compact"
      shape="pill"
    >
      {iconTone === "muted" ? null : <StatusIcon tone={iconTone} size="sm" />}
      {label}
    </Badge>
  );
}

export const statusBadgeWidget = {
  edit: StatusSelectEdit,
  read: StatusBadgeRead,
  cell: StatusBadgeRead,
} satisfies WidgetDefinition<string>;
