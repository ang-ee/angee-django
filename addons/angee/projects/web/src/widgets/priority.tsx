import type { ReactElement } from "react";

import {
  Glyph,
  StatusSelectEdit,
  cn,
  optionLabel,
  optionToken,
  toneFill,
  type WidgetDefinition,
  type WidgetRenderProps,
} from "@angee/ui";

/** Task urgency uses ascending bars, with an alert at the top of the scale. */
const PRIORITY_GLYPHS: Readonly<Record<string, { icon: string; className: string }>> = {
  urgent: { icon: "triangle-alert", className: toneFill.danger.ghost },
  high: { icon: "signal-high", className: "text-fg" },
  medium: { icon: "signal-medium", className: "text-fg" },
  low: { icon: "signal-low", className: "text-fg-muted" },
  none: { icon: "minus", className: "text-fg-subtle" },
};

function PriorityRead({ value, field }: WidgetRenderProps<string>): ReactElement | null {
  const token = optionToken(value);
  if (!token) return null;
  const glyph = PRIORITY_GLYPHS[token];
  const label = optionLabel(field?.options, value);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap",
        token === "none" ? "text-fg-subtle" : undefined,
      )}
    >
      {glyph ? (
        <Glyph decorative name={glyph.icon} className={cn("size-3.5 shrink-0", glyph.className)} />
      ) : null}
      <span>{label}</span>
    </span>
  );
}

function PriorityCell(props: WidgetRenderProps<string>): ReactElement | null {
  return optionToken(props.value) === "none" ? null : <PriorityRead {...props} />;
}

/** List cells hide unprioritised values; record reads keep their muted label. */
export const priorityWidget = {
  edit: StatusSelectEdit,
  read: PriorityRead,
  cell: PriorityCell,
} satisfies WidgetDefinition<string>;
