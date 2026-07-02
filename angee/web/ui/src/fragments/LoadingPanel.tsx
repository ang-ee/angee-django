import * as React from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { Card } from "../ui/card";
import { Skeleton, SkeletonText } from "../ui/skeleton";
import { textRoleVariants } from "../ui/text";

export interface LoadingPanelProps {
  message?: string;
  density?: "page" | "inline";
}

export function LoadingPanel({
  message,
  density = "page",
}: LoadingPanelProps): React.ReactElement {
  const t = useUiT();
  const label = message ?? t("loading.default");
  const inline = density === "inline";

  return (
    <div
      className={
        inline
          ? "grid min-h-24 place-content-center p-3"
          : "grid h-full place-content-center p-8"
      }
    >
      <Card
        aria-live="polite"
        className={
          inline
            ? "w-full px-4 py-3 shadow-none"
            : "w-72 px-6 py-5 shadow-none"
        }
        role="status"
      >
        <Skeleton className={inline ? "h-4 w-24" : "h-5 w-32"} />
        <SkeletonText className={inline ? "mt-3" : "mt-4"} lines={inline ? 2 : 3} />
        <p
          className={cn(
            textRoleVariants({ role: "meta" }),
            inline ? "mt-3" : "mt-4",
          )}
        >
          {label}
        </p>
      </Card>
    </div>
  );
}
