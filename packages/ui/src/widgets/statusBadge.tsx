import type { ReactElement, ReactNode } from "react";

import { Chip } from "../ui/chip";
import { statusLabel } from "../lib/labels";
import type { Tone } from "../lib/tones";
import { useStatusTone } from "./use-status-tone";
import { StatusSelectEdit } from "./statusSelectEdit";
import { canonicalOptionValue, optionLabel, type WidgetDefinition, type WidgetOption, type WidgetRenderProps } from "./types";

export type StatusDisplay = "pill" | "dot" | "text";

export interface StatusBadgeProps {
  /** The status value as stored (an enum member, a lifecycle word). */
  value: string | null | undefined;
  /** The authored option labels; a value outside them reads through `statusLabel`. */
  options?: readonly WidgetOption[];
  /** An explicit value → tone map (a `<Column tone>`); wins over the composed vocabulary. */
  tone?: Record<string, Tone>;
  /** `pill` (default): icon mark + soft fill; `dot`: a tone dot before plain text; `text`: toned text. */
  display?: StatusDisplay;
  /** Overrides the label the value resolves to. */
  children?: ReactNode;
  className?: string;
}

/**
 * The one status pill: resolves the value's tone through the app's composed status
 * vocabulary (`useStatusTone`) and its label through the authored options. The
 * `statusBadge` widget renders it for fields; surfaces outside the widget registry
 * (a derived run state, an upload stage, a claim state) render it directly instead
 * of keeping a tone map of their own.
 * @kind fragment
 */
export function StatusBadge({ value, options, tone: override, display = "pill", children, className }: StatusBadgeProps): ReactElement {
  const statusTone = useStatusTone();
  const label = children ?? (display === "dot"
    ? optionLabel(options, value)
    : canonicalOptionValue(options, value)
      ? optionLabel(options, value)
      : statusLabel(value ?? ""));
  if (!value && display !== "dot") return <span className={className}>—</span>;
  const tone = statusTone(value, override);
  return (
    <Chip
      className={className}
      tone={tone}
      mark={display === "pill" ? "icon" : display === "dot" ? "dot" : "none"}
      shape={display === "pill" ? "pill" : "rounded"}
      variant={display === "pill" ? "soft" : "ghost"}
    >
      {label}
    </Chip>
  );
}

function StatusBadgeRead({ value, field }: WidgetRenderProps<string>): ReactElement {
  return <StatusBadge value={value} options={field?.options} tone={field?.tone} display={field?.statusDisplay} />;
}

export const statusBadgeWidget = {
  edit: StatusSelectEdit,
  read: StatusBadgeRead,
  cell: StatusBadgeRead,
} satisfies WidgetDefinition<string>;
