import * as React from "react";

import { cn } from "../lib/cn";
import { Card } from "../ui/card";
import { Collapsible } from "../ui/collapsible";
import { SectionEyebrow } from "../ui/section-eyebrow";
import { textRoleVariants } from "../ui/text";

export interface FocusPanelProps {
  eyebrow?: React.ReactNode;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
  className?: string;
}

export function FocusPanel({
  eyebrow,
  title,
  subtitle,
  actions,
  children,
  collapsible = true,
  defaultOpen = true,
  className,
}: FocusPanelProps): React.ReactElement {
  const header = (
    <div className="flex flex-wrap items-start gap-3">
      <div className="min-w-0 flex-1">
        {eyebrow ? <SectionEyebrow>{eyebrow}</SectionEyebrow> : null}
        <h2 className="m-0 mt-1 truncate text-lg font-semibold text-fg">
          {title}
        </h2>
        {subtitle ? (
          <p className={cn(textRoleVariants({ role: "meta", truncate: true }), "m-0 mt-1")}>
            {subtitle}
          </p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {actions}
        {collapsible ? (
          <Collapsible.Trigger
            aria-label="Toggle details"
            className="justify-center px-1.5"
          >
            <Collapsible.Icon />
          </Collapsible.Trigger>
        ) : null}
      </div>
    </div>
  );

  if (!collapsible) {
    return (
      <Card asChild className={cn("px-4 py-3 shadow-none", className)}>
        <section>
          {header}
          <div className="mt-3">{children}</div>
        </section>
      </Card>
    );
  }

  return (
    <Collapsible.Root
      className={cn(
        "rounded-6 border border-border-subtle bg-sheet px-4 py-3 text-fg shadow-xs",
        className,
      )}
      defaultOpen={defaultOpen}
      render={<section />}
      variant="section"
    >
      {header}
      <Collapsible.Panel>{children}</Collapsible.Panel>
    </Collapsible.Root>
  );
}

