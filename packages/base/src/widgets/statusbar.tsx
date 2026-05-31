import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

const STATUS_STEPS = [
  { value: "DRAFT", label: "Draft" },
  { value: "IN_REVIEW", label: "In Review" },
  { value: "ACTIVE", label: "Active" },
  { value: "ARCHIVED", label: "Archived" },
] as const;

const STEP_CLIP =
  "[clip-path:polygon(0_0,calc(100%-12px)_0,100%_50%,calc(100%-12px)_100%,0_100%,12px_50%)]";
const FIRST_CLIP =
  "[clip-path:polygon(0_0,calc(100%-12px)_0,100%_50%,calc(100%-12px)_100%,0_100%)]";
const LAST_CLIP =
  "[clip-path:polygon(0_0,100%_0,100%_100%,0_100%,12px_50%)]";

function Statusbar({
  value,
  onChange,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  const current = statusIndex(value);
  const interactive = Boolean(onChange) && !readOnly;
  return (
    <div className="inline-flex items-stretch gap-px" role="list">
      {STATUS_STEPS.map((step, index) => {
        const currentStep = index === current;
        const completed = current >= 0 && index < current;
        return (
          <button
            key={step.value}
            type="button"
            role="listitem"
            aria-current={currentStep ? "step" : undefined}
            disabled={!interactive}
            className={cn(
              "inline-flex h-6 items-center text-xs font-medium outline-none transition-colors focus-visible:focus-ring disabled:opacity-100",
              index === 0 ? "pl-3.5 pr-4" : "pl-5 pr-4",
              index === 0
                ? FIRST_CLIP
                : index === STATUS_STEPS.length - 1
                  ? LAST_CLIP
                  : STEP_CLIP,
              currentStep && "bg-brand-soft text-brand-soft-text",
              completed && "bg-success-soft text-success-text",
              !currentStep && !completed && "bg-inset text-fg-muted",
              interactive ? "cursor-pointer hover:bg-sheet-2" : "cursor-default",
            )}
            onClick={() => onChange?.(step.value)}
          >
            {step.label}
          </button>
        );
      })}
    </div>
  );
}

export const statusbarWidget = {
  edit: Statusbar,
  read: Statusbar,
  cell: Statusbar,
} satisfies WidgetDefinition<string>;

function statusIndex(value: string | null | undefined): number {
  const normalised = normaliseStatus(value);
  return STATUS_STEPS.findIndex((step) => step.value === normalised);
}

function normaliseStatus(value: string | null | undefined): string {
  return String(value ?? "")
    .trim()
    .replace(/[\s-]+/g, "_")
    .toUpperCase();
}
