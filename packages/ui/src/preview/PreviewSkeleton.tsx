import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import { Skeleton, SkeletonStatus } from "../ui/skeleton";

export interface PreviewSkeletonProps {
  label: string;
  className?: string;
  variant?: "surface" | "text";
}

/** Reserve the preview viewport while its renderer or file body is loading.
 * @kind fragment
 */
export function PreviewSkeleton({
  label,
  className,
  variant = "surface",
}: PreviewSkeletonProps): ReactElement {
  return (
    <SkeletonStatus
      label={label}
      className={cn("h-full min-h-48 overflow-hidden bg-inset p-4", className)}
    >
      {variant === "text" ? (
        <div aria-hidden="true" className="mx-auto h-full max-w-4xl space-y-3 rounded-6 border border-border-subtle bg-sheet p-5">
          {Array.from({ length: 12 }, (_, index) => (
            <Skeleton
              key={index}
              shape="text"
              size="sm"
              className={[
                "w-11/12",
                "w-4/5",
                "w-2/3",
                "w-5/6",
              ][index % 4]}
            />
          ))}
        </div>
      ) : (
        <Skeleton aria-hidden="true" className="h-full min-h-40 w-full rounded-6" />
      )}
    </SkeletonStatus>
  );
}
