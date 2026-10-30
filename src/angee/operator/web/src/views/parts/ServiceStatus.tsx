import { StatusBadge, textRoleVariants } from "@angee/ui";
import type * as React from "react";

import { serviceVerdict } from "../../data/service-state";
import type { ServiceState } from "../../data/types";
import { useOperatorT } from "../../i18n";

export interface ServiceStatusProps {
  service: Pick<ServiceState, "status" | "outcome" | "exitCode" | "restarts">;
}

/** A service's verdict, with the exit status that ended it and how often the runtime restarted it. */
export function ServiceStatus({ service }: ServiceStatusProps): React.ReactNode {
  const t = useOperatorT();
  const notes = [
    service.exitCode != null && service.exitCode !== 0
      ? t("services.status.exit", { code: service.exitCode })
      : null,
    service.restarts > 0 ? t("services.status.restarts", { count: service.restarts }) : null,
  ].filter((note) => note !== null);

  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
      <StatusBadge value={serviceVerdict(service)} />
      {notes.length > 0 ? (
        <span className={textRoleVariants({ role: "meta" })}>{notes.join(" · ")}</span>
      ) : null}
    </span>
  );
}
