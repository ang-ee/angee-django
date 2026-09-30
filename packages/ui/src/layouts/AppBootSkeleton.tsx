import { useUiT } from "../i18n";
import { Skeleton, SkeletonStatus } from "../ui/skeleton";

/** Neutral first-paint shape shared by public and console entry routes. */
export function AppBootSkeleton() {
  const t = useUiT();
  return (
    <SkeletonStatus label={t("app.loading")} className="grid min-h-dvh place-items-center bg-canvas p-6 text-fg">
      <div className="w-full max-w-md space-y-4">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="h-40" />
      </div>
    </SkeletonStatus>
  );
}
