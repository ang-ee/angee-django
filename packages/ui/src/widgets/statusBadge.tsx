import type { ReactElement } from "react";

import { Badge } from "../ui/badge";
import { statusLabel } from "../lib/labels";
import { useStatusTone } from "./use-status-tone";
import { StatusSelectEdit } from "./statusSelectEdit";
import { canonicalOptionValue, optionLabel, type WidgetDefinition, type WidgetRenderProps } from "./types";

function StatusBadgeRead({
  value,
  field,
}: WidgetRenderProps<string>): ReactElement {
  const statusTone = useStatusTone();
  const display = field?.statusDisplay ?? "pill";
  const label = display === "dot"
    ? optionLabel(field?.options, value)
    : canonicalOptionValue(field?.options, value)
      ? optionLabel(field?.options, value)
      : statusLabel(value ?? "");
  if (!value && display !== "dot") return <span>—</span>;
  const tone = statusTone(value, field?.tone);
  return (
    <Badge
      tone={tone}
      density={display === "pill" ? "compact" : "bare"}
      mark={display === "pill" ? "icon" : display === "dot" ? "dot" : "none"}
      shape={display === "pill" ? "pill" : "rounded"}
      variant={display === "pill" ? "soft" : "ghost"}
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
