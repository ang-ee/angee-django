import * as React from "react";

import { cn } from "../lib/cn";
import { Card } from "../ui/card";

export interface HeroPanelProps {
  brand?: React.ReactNode;
  preview?: React.ReactNode;
  headline: React.ReactNode;
  body?: React.ReactNode;
  actions?: React.ReactNode;
  commandStrip?: React.ReactNode | null;
  className?: string;
}

export function HeroPanel({
  brand,
  preview,
  headline,
  body,
  actions,
  commandStrip = null,
  className,
}: HeroPanelProps): React.ReactElement {
  return (
    <Card asChild className={cn("px-8 py-10 shadow-none", className)}>
      <section className="flex min-h-0 flex-col justify-between gap-8">
        {brand || preview ? (
          <div className="space-y-3">
            {brand}
            {preview}
          </div>
        ) : null}
        <div className="min-w-0 flex-1 content-center space-y-5">
          <div className="text-2xl font-semibold leading-tight text-fg">
            {headline}
          </div>
          {body ? (
            <div className="max-w-prose text-sm leading-relaxed text-fg-2">
              {body}
            </div>
          ) : null}
          {actions ? (
            <div className="flex flex-wrap items-center gap-2">{actions}</div>
          ) : null}
        </div>
        {commandStrip ? <div>{commandStrip}</div> : null}
      </section>
    </Card>
  );
}

