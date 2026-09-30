import * as React from "react";

import { cn } from "../../lib/cn";
import { SectionEyebrow } from "../../ui/section-eyebrow";

export interface SectionHeadingProps {
  label: React.ReactNode;
  count?: React.ReactNode;
  summary?: React.ReactNode;
  hint?: React.ReactNode;
  audience?: React.ReactNode;
  as?: "h2" | "h3" | "span";
  className?: string;
}

/** Shared heading for record sections, tab bodies, and rail groups. */
export function SectionHeading({
  label,
  count,
  summary,
  hint,
  audience,
  as = "h3",
  className,
}: SectionHeadingProps): React.ReactElement {
  const Container = as === "span" ? "span" : "div";
  return (
    <Container className={cn("flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1", className)}>
      <SectionEyebrow as={as} tracking="wide" weight="semibold" tone="fg" className="shrink-0">
        {label}
      </SectionEyebrow>
      {count != null ? <span className="text-xs text-fg-muted">{count}</span> : null}
      {summary != null ? <span className="text-xs text-fg-muted">{summary}</span> : null}
      {hint != null ? <span className="text-xs text-fg-muted">{hint}</span> : null}
      {audience != null ? <span className="ml-auto text-xs text-fg-muted">{audience}</span> : null}
    </Container>
  );
}
