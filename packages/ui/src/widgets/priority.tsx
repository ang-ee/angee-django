import type { ReactElement } from "react";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { StatusSelectEdit } from "./statusSelectEdit";
import { optionLabel, optionToken, type WidgetDefinition, type WidgetRenderProps } from "./types";

/**
 * Urgency reads as a rank, so it gets a glyph that carries the rank — ascending
 * bars, with the top of the scale breaking the pattern. Text alone ("LOW",
 * "HIGH") makes the reader parse every row to find the urgent one.
 *
 * `none` is a real stored value, not an absence, but it says nothing: it renders
 * muted so a list of mostly-unprioritised rows stays quiet.
 */
const PRIORITY_GLYPHS: Readonly<Record<string, { icon: string; className: string }>> = {
  urgent: { icon: "triangle-alert", className: "text-danger-text" },
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

/** Rank-carrying renderer for a task's urgency; edits through the shared select. */
export const priorityWidget = {
  edit: StatusSelectEdit,
  read: PriorityRead,
  cell: PriorityRead,
} satisfies WidgetDefinition<string>;
