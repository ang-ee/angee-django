import type { ReactElement } from "react";

import { StatusDot } from "../ui/status-icon";
import { statusTone } from "./status-tones";
import { StatusSelectEdit } from "./statusSelectEdit";
import { optionLabel, type WidgetDefinition, type WidgetRenderProps } from "./types";

/**
 * The colored status dot: a `StatusDot` tinted by the shared `STATUS_TONES` vocabulary
 * (grey/green/red/amber for stopped/running/error/warning) next to the value's label.
 * The light-weight sibling of `statusBadge` — a bare dot, no pill — for a run state:
 * an agent or service running/stopped/error, or any small status enum (e.g. a task's
 * blocked/ready). A `<Column tone>` map overrides a value's tone; `edit` reuses the
 * select so the field is still writable in a form.
 */
function ColorDotRead({ value, field }: WidgetRenderProps<string>): ReactElement {
  const label = optionLabel(field?.options, value);
  // The dot carries an accessible label even when the value isn't a known option.
  const ariaLabel = typeof label === "string" && label ? label : String(value ?? "");
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusDot tone={statusTone(value, field?.tone)} label={ariaLabel || undefined} />
      <span>{label}</span>
    </span>
  );
}

export const colorDotWidget = {
  edit: StatusSelectEdit,
  read: ColorDotRead,
  cell: ColorDotRead,
} satisfies WidgetDefinition<string>;
