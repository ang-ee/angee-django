import type { ReactElement } from "react";

import { stateToneFromValue, type Tone, type ToneValueBuckets } from "../lib/tones";
import { Badge } from "../ui/badge";
import { Select } from "../ui/select";
import { widgetLabel } from "./label";
import { optionLabel, type WidgetDefinition, type WidgetRenderProps } from "./types";

/**
 * The status-badge widget's conventional `tone → values` vocabulary. It lives on
 * the widget (the thing whose job is "show a colored status"), not on the color
 * primitive (`lib/tones.ts` stays domain-free). A caller overrides any value with
 * an explicit `<Column tone={{ VALUE: "tone" }}>` map; spread `STATUS_BADGE_TONES`
 * to extend rather than replace the convention.
 */
export const STATUS_BADGE_TONES: ToneValueBuckets = {
  success: ["active", "published", "approved", "live", "open", "done"],
  warning: ["draft", "review", "pending", "in_review"],
  danger: ["error", "failed", "denied", "lost"],
  neutral: ["archived", "deleted", "rejected", "blocked"],
};

/**
 * Resolve a status value's tone. The caller's explicit `<Column tone>` entry wins
 * — keyed on the value exactly as it reads, the same exact-case lookup
 * `cellContent`/`BoardView` apply to the map. Unlike those plain cells (which fall
 * straight to `neutral`), the status-*badge* widget then layers its own
 * `STATUS_BADGE_TONES` convention for values the caller didn't map, since coloring
 * a status by convention is this widget's whole job.
 */
function statusBadgeTone(
  value: string | null | undefined,
  override: Record<string, Tone> | undefined,
): Tone {
  const mapped = value && override ? override[value] : undefined;
  if (mapped) return mapped;
  return stateToneFromValue(value ?? undefined, STATUS_BADGE_TONES);
}

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
  const label = optionLabel(field?.options, value);
  return (
    <Badge
      tone={statusBadgeTone(value, field?.tone)}
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
