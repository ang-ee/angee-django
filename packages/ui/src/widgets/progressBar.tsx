import type { ReactElement } from "react";

import { cn } from "../lib/cn";
import { tv } from "../lib/variants";
import { NumberField } from "../ui/number-field";
import { Slider } from "../ui/slider";
import { widgetLabel } from "./label";
import type { WidgetDefinition, WidgetRenderProps } from "./types";

type ProgressValue = number | null;

const progressBarVariants = tv({
  slots: {
    root: "inline-flex w-full min-w-[8rem] items-center gap-2",
    track: "h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-inset",
    fill: "h-full rounded-full bg-brand-soft-text transition-[width]",
    value: "w-10 shrink-0 text-right text-13 tabular-nums text-fg-muted",
  },
  variants: {
    density: {
      default: "",
      compact: {
        root: "min-w-[5rem] gap-1.5",
        track: "h-1.5",
        value: "w-8 text-xs",
      },
    },
  },
  defaultVariants: {
    density: "default",
  },
});

function ProgressBarEdit({
  value,
  onChange,
  field,
  readOnly,
}: WidgetRenderProps<ProgressValue>): ReactElement {
  const progress = normaliseProgress(value);

  if (readOnly) return <ProgressBarRead value={progress} />;

  return (
    <div className="flex w-full min-w-0 items-center gap-3">
      <Slider
        min={0}
        max={100}
        step={1}
        value={progress}
        disabled={readOnly}
        thumbLabel={widgetLabel(field, "Progress")}
        onValueChange={(next) => onChange?.(normaliseSliderProgress(next))}
      />
      <NumberField
        value={progress}
        min={0}
        max={100}
        step={1}
        snapOnStep
        className="w-20 shrink-0"
        inputProps={{
          "aria-label": widgetLabel(field, "Progress"),
          inputMode: "numeric",
        }}
        onValueChange={(next) => onChange?.(normaliseProgress(next))}
      />
    </div>
  );
}

function ProgressBarRead({
  value,
}: WidgetRenderProps<ProgressValue>): ReactElement {
  return <ProgressBarValue value={value} />;
}

function ProgressBarCell({
  value,
}: WidgetRenderProps<ProgressValue>): ReactElement {
  return <ProgressBarValue value={value} compact />;
}

export const progressBarWidget = {
  edit: ProgressBarEdit,
  read: ProgressBarRead,
  cell: ProgressBarCell,
} satisfies WidgetDefinition<ProgressValue>;

function ProgressBarValue({
  value,
  compact = false,
}: {
  value: ProgressValue | undefined;
  compact?: boolean;
}): ReactElement {
  const progress = normaliseProgress(value);
  const styles = progressBarVariants({
    density: compact ? "compact" : "default",
  });
  return (
    <span className={styles.root()}>
      <span
        className={styles.track()}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
      >
        <span
          className={cn(styles.fill(), toneClass(progress))}
          style={{ width: `${progress}%` }}
        />
      </span>
      <span className={styles.value()}>{progress}%</span>
    </span>
  );
}

function normaliseProgress(value: number | null | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, Math.round(value)));
}

function normaliseSliderProgress(value: number | readonly number[]): number {
  return normaliseProgress(Array.isArray(value) ? value[0] : value);
}

function toneClass(value: number): string {
  if (value >= 80) return "bg-success-text";
  if (value >= 40) return "bg-brand-soft-text";
  return "bg-warning-text";
}
