import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import type { WidgetDefinition, WidgetOption, WidgetRenderProps } from "./types";

const STEP_CLIP =
  "[clip-path:polygon(0_0,calc(100%-12px)_0,100%_50%,calc(100%-12px)_100%,0_100%,12px_50%)]";
const FIRST_CLIP =
  "[clip-path:polygon(0_0,calc(100%-12px)_0,100%_50%,calc(100%-12px)_100%,0_100%)]";
const LAST_CLIP =
  "[clip-path:polygon(0_0,100%_0,100%_100%,0_100%,12px_50%)]";

function Statusbar({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<string>): ReactElement {
  const steps = field?.options ?? [];
  const current = statusIndex(value, steps);
  const interactive = Boolean(onChange) && !readOnly;
  if (steps.length === 0) {
    return (
      <span className="inline-flex h-6 items-center rounded bg-inset px-2 text-xs font-medium text-fg-muted">
        {normaliseStatus(value)}
      </span>
    );
  }
  return (
    <div className="inline-flex items-stretch" role="list">
      {steps.map((step, index) => {
        const currentStep = index === current;
        const completed = current >= 0 && index < current;
        const disabled = !interactive || step.disabled;
        const tone = statusStepTone(currentStep, completed);
        const clip = index === 0
          ? FIRST_CLIP
          : index === steps.length - 1
            ? LAST_CLIP
            : STEP_CLIP;
        return (
          <button
            key={step.value}
            type="button"
            role="listitem"
            aria-current={currentStep ? "step" : undefined}
            disabled={disabled}
            className={cn(
              "group relative inline-flex h-6 items-stretch p-px text-xs font-medium outline-none transition-colors focus-visible:focus-ring disabled:opacity-100",
              index > 0 && "-ml-3",
              currentStep ? "z-20" : completed ? "z-10" : "z-0",
              clip,
              tone.border,
              !disabled ? "cursor-pointer" : "cursor-default",
            )}
            onClick={() => onChange?.(step.value)}
          >
            <span
              className={cn(
                "inline-flex h-full items-center transition-colors",
                index === 0 ? "pl-3.5 pr-4" : "pl-5 pr-4",
                clip,
                tone.fill,
                !disabled && tone.hover,
              )}
            >
              {step.label}
            </span>
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

function statusIndex(
  value: string | null | undefined,
  steps: readonly WidgetOption[],
): number {
  const normalised = normaliseStatus(value);
  return steps.findIndex((step) => normaliseStatus(step.value) === normalised);
}

function normaliseStatus(value: string | null | undefined): string {
  return String(value ?? "")
    .trim()
    .replace(/[\s-]+/g, "_")
    .toUpperCase();
}

function statusStepTone(
  current: boolean,
  completed: boolean,
): { border: string; fill: string; hover: string } {
  if (current) {
    return {
      border: "bg-brand",
      fill: "bg-brand-soft text-brand-soft-text",
      hover: "group-hover:bg-brand-soft",
    };
  }
  if (completed) {
    return {
      border: "bg-success-text",
      fill: "bg-success-soft text-success-text",
      hover: "group-hover:bg-success-soft",
    };
  }
  return {
    border: "bg-border-strong",
    fill: "bg-inset text-fg-muted",
    hover: "group-hover:bg-sheet-2",
  };
}
