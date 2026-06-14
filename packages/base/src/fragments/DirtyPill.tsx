import * as React from "react";

import { type Tone } from "../lib/tones";
import {
  Badge,
  type BadgeDensity,
  type BadgeShape,
} from "../ui/badge";
import { Spinner } from "../ui/spinner";

export type DirtyPillState = "dirty" | "saving" | "saved";

const dirtyPillTones: Record<DirtyPillState, Tone> = {
  dirty: "warning",
  saved: "success",
  saving: "neutral",
};

const dirtyPillLabels: Record<DirtyPillState, React.ReactNode> = {
  dirty: "Unsaved",
  saved: "Saved",
  saving: "Saving",
};

export interface DirtyPillProps {
  className?: string;
  density?: BadgeDensity;
  labels?: Partial<Record<DirtyPillState, React.ReactNode>>;
  shape?: BadgeShape;
  state: DirtyPillState;
}

export const DirtyPill = React.forwardRef<HTMLSpanElement, DirtyPillProps>(
  function DirtyPill(
    {
      className,
      density = "compact",
      labels,
      shape = "pill",
      state,
    },
    ref,
  ) {
    const label = labels?.[state] ?? dirtyPillLabels[state];

    return (
      <Badge
        ref={ref}
        aria-live={state === "saving" ? "polite" : undefined}
        className={className}
        density={density}
        shape={shape}
        tone={dirtyPillTones[state]}
      >
        {state === "saving" ? <Spinner aria-hidden="true" size="sm" /> : null}
        {label}
      </Badge>
    );
  },
);
DirtyPill.displayName = "DirtyPill";
