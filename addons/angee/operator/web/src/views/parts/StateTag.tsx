import { Chip, StatusDot, statusLabel, useStatusTone } from "@angee/ui";
import type * as React from "react";

export interface StateTagProps {
  state: string;
}

export function StateTag({ state }: StateTagProps): React.ReactNode {
  const statusTone = useStatusTone();
  const tone = statusTone(state, undefined, { unknownTone: "neutral" });
  const label = statusLabel(state.trim() || "unknown");

  return (
    <Chip tone={tone}>
      <StatusDot size="sm" tone={tone} />
      {label}
    </Chip>
  );
}
