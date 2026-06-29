import * as React from "react";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { Card } from "../ui/card";
import { Skeleton, SkeletonText } from "../ui/skeleton";
import { textRoleVariants } from "../ui/text";

export interface LoadingPanelProps {
  message?: string;
}

export function LoadingPanel({ message }: LoadingPanelProps): React.ReactElement {
  const t = useBaseT();
  const label = message ?? t("loading.default");

  return (
    <div className="grid h-full place-content-center p-8">
      <Card
        aria-live="polite"
        className="w-72 px-6 py-5 shadow-none"
        role="status"
      >
        <Skeleton className="h-5 w-32" />
        <SkeletonText className="mt-4" lines={3} />
        <p className={cn(textRoleVariants({ role: "meta" }), "mt-4")}>{label}</p>
      </Card>
    </div>
  );
}
