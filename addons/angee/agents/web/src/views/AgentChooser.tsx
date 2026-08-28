import * as React from "react";
import { SelectPrimitive, StatusDot, statusTone } from "@angee/ui";

import { useAgentsT } from "../i18n";
import type { AcpStatus } from "../useAcpRuntime";
import type { AgentRosterItem } from "../documents";

/**
 * The dense top-bar agent switcher: a Base UI Select listbox over the running agents.
 *
 * This is a SESSION/THREAD switcher, not a model picker — each agent owns its own durable
 * ACP session + transcript, so selecting one swaps which already-mounted per-agent runtime
 * is shown (the caller drives `onSelect` into its keep-alive state, never into a child's
 * `agentId` prop). The trigger shows the LIVE connection status of the visible agent (its
 * `AcpStatus` dot, pulsing only while `connecting`), then the agent name · muted model
 * handle. List rows show each agent's persisted server `runtime_status` dot (always RUNNING
 * here) — a different vocabulary from the connection status, so it carries the runtime label.
 *
 * It renders NO `role="status"` live region: `AgentChat` owns exactly one. Base UI marks the
 * active row `aria-selected` and renders its `ItemIndicator` automatically.
 */
export function AgentChooser({
  agents,
  value,
  onSelect,
  status,
  statusLabel,
  fallbackName,
  fallbackHandle,
}: {
  agents: readonly AgentRosterItem[];
  value: string;
  onSelect: (id: string) => void;
  status: AcpStatus;
  statusLabel: string;
  fallbackName?: string;
  fallbackHandle?: string;
}): React.ReactElement {
  const t = useAgentsT();
  // Tolerate a value absent from the list (default agent not yet listed, or the roster
  // still loading) via the fallbacks, so the trigger never renders blank.
  const selected = agents.find((agent) => agent.id === value);
  const name = selected?.name ?? fallbackName ?? "";
  const handle = selected?.model?.name ?? fallbackHandle;
  const items = React.useMemo(
    () => agents.map((agent) => ({ value: agent.id, label: agent.name })),
    [agents],
  );

  return (
    <SelectPrimitive.Root
      items={items}
      value={value}
      onValueChange={(next) => {
        if (next) onSelect(next);
      }}
    >
      <SelectPrimitive.Trigger
        size="sm"
        aria-label={t("chat.switchAgent")}
        className="w-auto max-w-[18rem] gap-1.5 border-transparent bg-transparent hover:border-transparent hover:bg-inset"
      >
        <StatusDot
          tone={statusTone(status, { closed: "danger" })}
          label={statusLabel}
          className={status === "connecting" ? "motion-safe:animate-pulse" : undefined}
        />
        <span className="min-w-0 flex-1 truncate text-left text-13 font-medium text-fg">
          {name}
          {handle ? <span className="font-normal text-fg-muted"> · {handle}</span> : null}
        </span>
        <SelectPrimitive.Icon />
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Positioner sideOffset={4} align="start">
          <SelectPrimitive.Content size="sm" className="min-w-[18rem]">
            <SelectPrimitive.List>
              {agents.map((agent) => (
                <SelectPrimitive.Item key={agent.id} value={agent.id} label={agent.name} size="sm">
                  <StatusDot
                    tone={statusTone(agent.runtime_status)}
                    label={t("chat.running")}
                  />
                  <SelectPrimitive.ItemText>
                    {agent.name}
                    {agent.model?.name ? ` · ${agent.model.name}` : ""}
                  </SelectPrimitive.ItemText>
                  <SelectPrimitive.ItemIndicator />
                </SelectPrimitive.Item>
              ))}
            </SelectPrimitive.List>
          </SelectPrimitive.Content>
        </SelectPrimitive.Positioner>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}
