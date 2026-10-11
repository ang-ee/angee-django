import { EmptyState, Select, type SelectChoice } from "@angee/ui";
import { useMemo, useState, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { ServiceLogs } from "./logs";

/**
 * The logs drawer body: a service selector over the live snapshot, then the
 * selected service's log tail. The drawer is not route-scoped, so it picks its
 * own service and opens no socket until one is picked. The shell mounts this
 * only while the drawer is open, so streaming starts on open and the tail
 * survives navigation (the overlay is mounted above the router outlet).
 */
function OperatorLogsDrawerBody(): ReactNode {
  const t = useOperatorT();
  const { snapshot } = useOperatorSnapshot({ services: true });
  const [selected, setSelected] = useState("");

  const options = useMemo<readonly SelectChoice[]>(
    () =>
      (snapshot?.services ?? []).map((service) => ({
        value: service.name,
        label: service.name,
      })),
    [snapshot],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <Select
        options={options}
        value={selected}
        onValueChange={setSelected}
        placeholder={t("logs.target.placeholder")}
        aria-label={t("logs.target.label")}
      />
      {selected ? (
        <ServiceLogs name={selected} title={selected} />
      ) : (
        <EmptyState
          fill
          icon="operator-logs"
          title={t("logs.target.empty.title")}
          description={t("logs.target.empty.description")}
        />
      )}
    </div>
  );
}

/**
 * The operator logs drawer — the first console-shell drawer adopter. Mounted at
 * shell level under the console's persistent operator transport.
 */
export function OperatorLogsDrawer(): ReactNode {
  return <OperatorLogsDrawerBody />;
}
