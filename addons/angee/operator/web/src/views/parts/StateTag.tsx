import { Badge, StatusDot, statusLabel, statusTone } from "@angee/base";
import type * as React from "react";

export interface StateTagProps {
  state: string;
}

export function StateTag({ state }: StateTagProps): React.ReactNode {
  const tone = statusTone(state, undefined, { unknownTone: "neutral" });
  const label = statusLabel(state.trim() || "unknown");

  return (
    <Badge density="compact" shape="pill" tone={tone}>
      <StatusDot size="sm" tone={tone} />
      {label}
    </Badge>
  );
}
