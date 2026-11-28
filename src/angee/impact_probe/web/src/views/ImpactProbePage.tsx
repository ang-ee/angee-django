import { EmptyState } from "@angee/ui";

import { useImpactProbeT } from "../i18n";

export function ImpactProbePage() {
  const t = useImpactProbeT();
  return <EmptyState fill icon="file" title={t("page.empty")} />;
}
