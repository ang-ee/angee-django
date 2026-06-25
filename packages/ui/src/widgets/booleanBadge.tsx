import type { ReactElement } from "react";

import { Badge } from "../ui/badge";
import type { WidgetDefinition, WidgetOption, WidgetRenderProps } from "./types";

function BooleanBadgeRead({
  value,
  field,
}: WidgetRenderProps<boolean | string | number>): ReactElement {
  const active = booleanValue(value);
  return (
    <Badge
      tone={active ? "success" : "neutral"}
      density="compact"
      shape="pill"
    >
      {booleanLabel(active, field?.options)}
    </Badge>
  );
}

/** Badge renderer for boolean fields, with true/false labels from options. */
export const booleanBadgeWidget = {
  read: BooleanBadgeRead,
  cell: BooleanBadgeRead,
} satisfies WidgetDefinition<boolean | string | number>;

function booleanValue(value: boolean | string | number | null | undefined): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    return ["1", "true", "yes", "y", "on"].includes(value.trim().toLowerCase());
  }
  return false;
}

function booleanLabel(
  active: boolean,
  options: readonly WidgetOption[] | undefined,
): WidgetOption["label"] {
  const option = options?.find(
    (candidate) => booleanValue(candidate.value) === active,
  );
  return option?.label ?? (active ? "True" : "False");
}
