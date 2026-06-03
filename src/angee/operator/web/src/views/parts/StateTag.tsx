import {
  Badge,
  StatusDot,
  type BadgeVariant,
  type StatusDotTone,
} from "@angee/base";
import type * as React from "react";

export interface StateTagProps {
  state: string;
}

type StateIntent = "success" | "muted" | "danger" | "warning" | "neutral";

const STATE_INTENT_BY_NAME: Readonly<Partial<Record<string, StateIntent>>> = {
  active: "success",
  default: "neutral",
  error: "danger",
  failed: "danger",
  idle: "muted",
  pending: "warning",
  running: "success",
  starting: "warning",
  stopped: "muted",
  unknown: "neutral",
  up: "success",
};

// TODO(S2): Badge.variant and StatusDot.tone do not expose literal muted/neutral values yet.
const BADGE_VARIANT_BY_INTENT = {
  danger: "danger",
  muted: "default",
  neutral: "default",
  success: "success",
  warning: "warning",
} satisfies Record<StateIntent, BadgeVariant>;

const DOT_TONE_BY_INTENT = {
  danger: "danger",
  muted: "default",
  neutral: "default",
  success: "success",
  warning: "warning",
} satisfies Record<StateIntent, StatusDotTone>;

export function StateTag({ state }: StateTagProps): React.ReactNode {
  const intent = stateIntent(state);
  const label = stateLabel(state);

  return (
    <Badge
      density="compact"
      shape="pill"
      variant={BADGE_VARIANT_BY_INTENT[intent]}
    >
      <StatusDot size="sm" tone={DOT_TONE_BY_INTENT[intent]} />
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
