import * as React from "react";

import { cn } from "../../lib/cn";
import { textRoleVariants } from "../../ui/text";
import { SectionEyebrow } from "../../ui/section-eyebrow";

export interface SectionHeadingProps {
  label: React.ReactNode;
  presentation?: "eyebrow" | "title";
  count?: React.ReactNode;
  summary?: React.ReactNode;
  hint?: React.ReactNode;
  audience?: React.ReactNode;
  as?: "h2" | "h3" | "span";
  className?: string;
}

/** Shared heading for record sections, tab bodies, and rail groups.
 * @kind fragment
 */
export function SectionHeading({
  label,
  presentation = "eyebrow",
  count,
  summary,
  hint,
  audience,
  as = "h3",
  className,
}: SectionHeadingProps): React.ReactElement {
  const Container = as === "span" ? "span" : "div";
  const Heading = as;
  return (
    <Container className={cn("flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1", className)}>
      {presentation === "title" ? (
        <Heading className={cn("m-0", textRoleVariants({ role: "title" }))}>{label}</Heading>
      ) : (
        <SectionEyebrow as={as} tracking="wide" weight="semibold" tone="fg" className="shrink-0">
          {label}
        </SectionEyebrow>
      )}
      {count != null ? <span className="text-12 text-fg-muted">{count}</span> : null}
      {summary != null ? <span className="text-12 text-fg-muted">{summary}</span> : null}
      {hint != null ? <span className="text-12 text-fg-muted">{hint}</span> : null}
      {audience != null ? <span className="ml-auto text-12 text-fg-muted">{audience}</span> : null}
    </Container>
  );
}
