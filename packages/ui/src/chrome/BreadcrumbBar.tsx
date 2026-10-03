import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import { barVariants } from "../layouts/bar";
import { Breadcrumb } from "./Breadcrumb";

/** The narrow trail directly below the top bar, on the top bar's surface. */
export function BreadcrumbBar({ className }: { className?: string }): ReactElement {
  return <div data-console-breadcrumbs className={cn(
    barVariants({ height: "breadcrumbs", edge: "none", tone: "rail", pad: "flush" }),
    "area-breadcrumbs overflow-hidden", className,
  )}>
    <Breadcrumb tone="rail" className="text-xs" />
  </div>;
}
