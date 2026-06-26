import * as React from "react";

import { cn } from "../lib/cn";
import { type Tone } from "../lib/tones";
import { Badge } from "../ui/badge";
import { Card } from "../ui/card";

export interface ListPanelProps {
  title: React.ReactNode;
  summary?: React.ReactNode;
  empty?: React.ReactNode;
  actions?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}

export interface ListItemStatus {
  label: React.ReactNode;
  tone?: Tone;
}

export interface ListItemProps {
  title: React.ReactNode;
  meta?: React.ReactNode;
  status?: ListItemStatus;
  tags?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}

export function ListPanel({
  title,
  summary,
  empty,
  actions,
  children,
  className,
}: ListPanelProps): React.ReactElement {
  const hasChildren = React.Children.count(children) > 0;

  return (
    <Card asChild className={cn("overflow-hidden shadow-none", className)}>
      <section>
        <div className="flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-15 font-semibold text-fg">{title}</h2>
            {summary ? (
              <p className="mt-0.5 text-2xs text-fg-muted">{summary}</p>
            ) : null}
          </div>
          {actions ? (
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          ) : null}
        </div>
        <div className="divide-y divide-border-subtle">
          {hasChildren ? (
            children
          ) : (
            <div className="px-4 py-6 text-center text-13 text-fg-muted">
              {empty}
            </div>
          )}
        </div>
      </section>
    </Card>
  );
}

export function ListItem({
  title,
  meta,
  status,
  tags,
  actions,
  className,
}: ListItemProps): React.ReactElement {
  return (
    <div className={cn("flex items-center justify-between gap-3 px-4 py-3", className)}>
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="min-w-0 truncate text-13 font-medium text-fg">
            {title}
          </span>
          {status ? (
            <Badge tone={status.tone ?? "neutral"}>{status.label}</Badge>
          ) : null}
        </div>
        {meta ? (
          <p className="m-0 mt-0.5 truncate text-2xs text-fg-muted">{meta}</p>
        ) : null}
      </div>
      {tags || actions ? (
        <div className="flex shrink-0 items-center gap-2">
          {tags}
          {actions}
        </div>
      ) : null}
    </div>
  );
}

