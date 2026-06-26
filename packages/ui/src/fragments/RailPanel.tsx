import * as React from "react";

import { cn } from "../lib/cn";
import { Badge } from "../ui/badge";
import { Card } from "../ui/card";

export interface RailPanelProps {
  title: React.ReactNode;
  count?: React.ReactNode;
  fetching?: boolean;
  empty?: React.ReactNode;
  actions?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}

export function RailPanel({
  title,
  count,
  fetching = false,
  empty,
  actions,
  children,
  className,
}: RailPanelProps): React.ReactElement {
  const hasChildren = React.Children.count(children) > 0;

  return (
    <Card asChild className={cn("p-3 shadow-none", className)}>
      <section>
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-fg">{title}</h3>
            {count !== undefined ? <Badge>{count}</Badge> : null}
            {fetching ? <Badge tone="info">Refreshing</Badge> : null}
          </div>
          {actions ? (
            <div className="flex shrink-0 items-center gap-1">{actions}</div>
          ) : null}
        </div>
        {hasChildren ? (
          children
        ) : (
          <div className="py-4 text-center text-13 text-fg-muted">{empty}</div>
        )}
      </section>
    </Card>
  );
}

