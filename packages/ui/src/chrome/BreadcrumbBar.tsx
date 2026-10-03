import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import { barVariants } from "../layouts/bar";
import { Breadcrumb } from "./Breadcrumb";

/** The sheet-surface trail directly below the top bar. */
export function BreadcrumbBar({ className }: { className?: string }): ReactElement {
  return <div data-console-breadcrumbs className={cn(
    barVariants({ height: "breadcrumbs", edge: "bottom", tone: "sheet", pad: "flush", text: "13-muted" }),
    "area-breadcrumbs overflow-hidden", className,
  )}>
    <Breadcrumb />
  </div>;
}
