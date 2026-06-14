import { Badge, StatusDot, type Tone } from "@angee/base";
import type * as React from "react";

export interface StateTagProps {
  state: string;
}

type StateIntent = Extract<
  Tone,
  "success" | "neutral" | "danger" | "warning"
>;

const STATE_INTENT_BY_NAME: Readonly<Partial<Record<string, StateIntent>>> = {
  active: "success",
  default: "neutral",
  error: "danger",
  failed: "danger",
  idle: "neutral",
  pending: "warning",
  running: "success",
  starting: "warning",
  stopped: "neutral",
  unknown: "neutral",
  up: "success",
};

export function StateTag({ state }: StateTagProps): React.ReactNode {
  const intent = stateIntent(state);
  const label = stateLabel(state);

  return (
    <Badge density="compact" shape="pill" tone={intent}>
      <StatusDot size="sm" tone={intent} />
      {label}
    </Badge>
  );
}

function stateIntent(state: string): StateIntent {
  return STATE_INTENT_BY_NAME[state.trim().toLowerCase()] ?? "neutral";
}

function stateLabel(state: string): string {
  const label = state.trim();
  if (!label) return "Unknown";

  return label
    .split(/[\s_-]+/)
    .filter((part) => part.length > 0)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
