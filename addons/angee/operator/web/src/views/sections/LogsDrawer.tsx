import { EmptyState, Select, type SelectChoice } from "@angee/ui";
import { useMemo, useState, type ReactNode } from "react";

import {
  WORKSPACE_LOGS_QUERY,
  WORKSPACE_LOGS_SUBSCRIPTION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import {
  OperatorTransportProvider,
  useOperatorSnapshot,
} from "../../data/transport";
import { LogPanel, useDaemonLogStream, useServiceLogStream } from "./logs";

// One drawer log source: a running service (structured `/logs/stream` socket) or
// a workspace (graphql history + live tail). Encoded `<kind>:<name>` so one
// <Select> spans both kinds; parsed back to pick the stream.
type LogTarget = { kind: "service" | "workspace"; name: string };

function parseTarget(value: string): LogTarget | null {
  const sep = value.indexOf(":");
  if (sep < 0) return null;
  const kind = value.slice(0, sep);
  const name = value.slice(sep + 1);
  if ((kind !== "service" && kind !== "workspace") || !name) return null;
  return { kind, name };
}

/**
 * The logs drawer body: a service/workspace selector over the live snapshot, then
 * the selected target's log tail. The drawer is not route-scoped, so it picks its
 * own target. Both stream hooks run every render (rules of hooks), but each is
 * inert unless its kind is selected — an `undefined` name opens no socket and no
 * subscription, so the drawer connects only once a target is picked. The shell
 * mounts this only while the drawer is open, so streaming starts on open and the
 * tail survives navigation (the overlay is mounted above the router outlet).
 */
function OperatorLogsDrawerBody(): ReactNode {
  const t = useOperatorT();
  const { snapshot } = useOperatorSnapshot({ services: true, workspaces: true });
  const [selected, setSelected] = useState("");

  const options = useMemo<readonly SelectChoice[]>(
    () => [
      ...(snapshot?.services ?? []).map((service) => ({
        value: `service:${service.name}`,
        label: service.name,
      })),
      ...(snapshot?.workspaces ?? []).map((workspace) => ({
        value: `workspace:${workspace.name}`,
        label: workspace.name,
      })),
    ],
    [snapshot],
  );

  const target = parseTarget(selected);
  const serviceLogs = useServiceLogStream(
    target?.kind === "service" ? target.name : undefined,
  );
  const workspaceLogs = useDaemonLogStream({
    name: target?.kind === "workspace" ? target.name : undefined,
    historyQuery: WORKSPACE_LOGS_QUERY,
    historyField: "workspaceLogs",
    streamSubscription: WORKSPACE_LOGS_SUBSCRIPTION,
    streamField: "onWorkspaceLogs",
  });
  const logs = target?.kind === "workspace" ? workspaceLogs : serviceLogs;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <Select
        options={options}
        value={selected}
        onValueChange={setSelected}
        placeholder={t("logs.target.placeholder")}
        aria-label={t("logs.target.label")}
      />
      {target ? (
        <LogPanel logs={logs} title={target.name} />
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
 * shell level (above the router outlet), outside the operator routes' transport
 * gate, so it establishes its own daemon transport rather than depending on an
 * operator page being open.
 */
export function OperatorLogsDrawer(): ReactNode {
  return (
    <OperatorTransportProvider>
      <OperatorLogsDrawerBody />
    </OperatorTransportProvider>
  );
}
