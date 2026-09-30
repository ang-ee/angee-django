import * as React from "react";
import { Check, ChevronDown, Pencil } from "lucide-react";

import { cn } from "../lib/cn";
import { useUiT } from "../i18n";
import { statusLabel } from "../lib/labels";
import { Badge } from "../ui/badge";
import { DropdownMenu } from "../ui/dropdown-menu";
import { Skeleton, SkeletonStatus } from "../ui/skeleton";
import { formatDate, formatDateRange, type DateFormatValue } from "./date-format";
import { canonicalOptionValue, optionLabel, optionTextLabel, type WidgetDefinition, type WidgetOption, type WidgetRenderProps } from "./types";

const STEP_CLIP = "[clip-path:polygon(0_0,calc(100%-12px)_0,100%_50%,calc(100%-12px)_100%,0_100%,12px_50%)]";
const FIRST_CLIP = "[clip-path:polygon(0_0,calc(100%-12px)_0,100%_50%,calc(100%-12px)_100%,0_100%)]";
const LAST_CLIP = "[clip-path:polygon(0_0,100%_0,100%_100%,0_100%,12px_50%)]";

/** Presentation and server-owned eligibility for one statusbar step. */
export interface StatusbarStep extends WidgetOption {
  startDate?: DateFormatValue;
  endDate?: DateFormatValue;
  note?: React.ReactNode;
  date?: DateFormatValue;
  editableDates?: boolean;
}

export interface StatusbarStepsProps {
  steps: readonly StatusbarStep[];
  value: string | null | undefined;
  onChange?: (value: string) => void;
  onEditDates?: (value: string) => void;
  /** Blocks phase selection; date editing follows each step's own permission. */
  readOnly?: boolean;
  fill?: boolean;
  /** Available width supplied by a slot host; otherwise ResizeObserver measures it. */
  containerWidth?: number;
  /** A lifecycle owner can supply a terminal state outside the step collection. */
  offPath?: { label: React.ReactNode; date?: DateFormatValue };
  "aria-label"?: string;
}

function Statusbar({ value, onChange, field, readOnly }: WidgetRenderProps<string>): React.ReactElement {
  const steps = field?.options ?? [];
  if (steps.length === 0) {
    return <span className="inline-flex h-6 items-center rounded-6 bg-inset px-2 text-xs font-medium text-fg-muted">{value ? statusLabel(value) : ""}</span>;
  }
  return <StatusbarSteps steps={steps.map((step) => ({ ...step, selectable: step.selectable ?? true }))}
    value={value} onChange={onChange} readOnly={readOnly} fill={field?.fill} containerWidth={field?.containerWidth} />;
}

/** A bar-shaped loading state with the same chevron rhythm as loaded steps. */
export function StatusbarSkeleton({ count = 4, fill = false, twoLine = false, label }: {
  count?: number; fill?: boolean; twoLine?: boolean; label?: string;
}): React.ReactElement {
  const t = useUiT();
  return <SkeletonStatus label={label ?? t("statusbar.loading")} className={cn("isolate flex items-stretch", twoLine ? "h-12" : "h-8", fill ? "w-full" : "w-fit")}>
    {Array.from({ length: count }, (_, index) => <Skeleton key={index}
      className={cn("h-full w-24", fill && "min-w-0 flex-1", index > 0 && "-ml-2.5", index === 0 ? FIRST_CLIP : index === count - 1 ? LAST_CLIP : STEP_CLIP)} />)}
  </SkeletonStatus>;
}

/** The shared chevron statusbar. Owners supply path and selectability facts. */
export function StatusbarSteps({ steps, value, onChange, onEditDates, readOnly, fill = false, containerWidth,
  offPath, "aria-label": ariaLabel }: StatusbarStepsProps): React.ReactElement {
  const t = useUiT();
  const path = steps.filter((step) => step.onPath !== false);
  const currentValue = canonicalOptionValue(steps, value);
  const current = path.findIndex((step) => step.value === currentValue);
  const sideStep = steps.find((step) => step.value === currentValue && step.onPath === false);
  const terminal: StatusbarStepsProps["offPath"] = offPath ?? (sideStep ? { label: sideStep.label, date: sideStep.date } : undefined);
  const [observedWidth, setObservedWidth] = React.useState<number>();
  const [requiredWidth, setRequiredWidth] = React.useState<number>();
  const hostRef = React.useRef<HTMLDivElement>(null);
  const barRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const host = hostRef.current;
    const bar = barRef.current;
    if (!host || !bar) return;
    const measure = () => {
      setObservedWidth(host.parentElement?.clientWidth ?? host.clientWidth);
      setRequiredWidth(bar.scrollWidth);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(host.parentElement ?? host);
    observer.observe(bar);
    return () => observer.disconnect();
  }, [path.length, fill]);
  const available = containerWidth ?? observedWidth;
  const collapsed = available !== undefined && requiredWidth !== undefined && requiredWidth > available;
  const active = current >= 0 ? path[current] : undefined;
  const twoLine = path.some((step, index) => Boolean(dateRange(step) || (index === current && step.note)));
  const selectable = (step: StatusbarStep) => !readOnly && Boolean(onChange) && step.selectable === true && !step.disabled && step.value !== currentValue;
  const choose = (step: StatusbarStep) => { if (selectable(step)) onChange?.(step.value); };
  // No step and no state to name (a project with no milestones yet): nothing to show.
  if (path.length === 0 && terminal === undefined) return <></>;
  return <div ref={hostRef} className={cn("relative min-w-0", fill ? "w-full" : "inline-block max-w-full")}>
    <div ref={barRef} role="list" aria-label={ariaLabel} aria-hidden={collapsed || undefined}
      className={cn("isolate flex w-max items-stretch", twoLine ? "h-12" : "h-8", fill && "min-w-full", collapsed && "pointer-events-none invisible absolute left-0 top-0")}>
      {path.map((step, index) => {
        const isCurrent = current === index;
        const complete = current >= 0 && index < current;
        const canSelect = selectable(step);
        const detail = [dateRange(step), isCurrent ? step.note : undefined].filter(Boolean);
        return <div key={step.value} role="listitem" aria-current={isCurrent ? "step" : undefined}
          className={cn("group relative min-w-0 p-px text-xs font-medium", fill ? "flex-1" : "flex-none",
            index > 0 && "-ml-2.5", isCurrent ? "z-20" : complete ? "z-10" : "z-0",
            index === 0 ? FIRST_CLIP : index === path.length - 1 ? LAST_CLIP : STEP_CLIP,
            terminal ? "bg-border-strong" : isCurrent ? "bg-brand" : complete ? "bg-success" : "bg-border-strong")}>
          <div className={cn("flex h-full min-h-7 items-center gap-1.5", index === 0 ? "pl-3.5 pr-4" : "pl-5 pr-4",
            index === 0 ? FIRST_CLIP : index === path.length - 1 ? LAST_CLIP : STEP_CLIP,
            terminal ? "bg-inset text-fg-muted" : isCurrent ? "bg-brand text-on-brand" : complete ? "bg-success-soft text-success-text" : "bg-inset text-fg-muted")}>
            {complete && !terminal ? <Check aria-hidden="true" className="size-3.5 shrink-0" /> : null}
            <button type="button" disabled={!canSelect} onClick={() => choose(step)}
              className={cn("min-w-0 text-left outline-none focus-visible:focus-ring disabled:cursor-default", canSelect && "cursor-pointer underline-offset-2 hover:underline")}>
              <span className="block truncate">{step.label}</span>
              {detail.length > 0 ? <span className="block truncate text-[11px] font-normal opacity-80">{detail.map((part, partIndex) =>
                <React.Fragment key={partIndex}>{partIndex > 0 ? " · " : ""}{part}</React.Fragment>)}</span> : null}
            </button>
            {step.editableDates && onEditDates ? <button type="button"
              aria-label={t("statusbar.editDates", { label: optionTextLabel(step.label, step.value) })}
              onClick={() => onEditDates(step.value)} className="ml-auto shrink-0 rounded-4 p-0.5 outline-none focus-visible:focus-ring">
              <Pencil aria-hidden="true" className="size-3" />
            </button> : null}
          </div>
        </div>;
      })}
    </div>
    {collapsed ? <DropdownMenu.Root><DropdownMenu.Trigger render={<button type="button"
      className="inline-flex h-8 max-w-full items-center gap-2 rounded-6 border border-border-strong bg-inset px-3 text-xs text-fg outline-none focus-visible:focus-ring" />}>
      <span className="truncate">{terminal?.label ?? active?.label ?? optionLabel(steps, value)}</span>
      <span className="shrink-0 text-fg-muted">{t("statusbar.position", { current: current >= 0 ? current + 1 : "–", total: path.length })}</span>
      <ChevronDown aria-hidden="true" className="size-3.5 shrink-0" />
    </DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Positioner><DropdownMenu.Content>
      {path.map((step) => <React.Fragment key={step.value}>
        <DropdownMenu.Item disabled={!selectable(step)} onClick={() => choose(step)}>{step.label}</DropdownMenu.Item>
        {step.editableDates && onEditDates ? <DropdownMenu.Item onClick={() => onEditDates(step.value)}>
          {t("statusbar.editDates", { label: optionTextLabel(step.label, step.value) })}
        </DropdownMenu.Item> : null}
      </React.Fragment>)}
    </DropdownMenu.Content></DropdownMenu.Positioner></DropdownMenu.Portal></DropdownMenu.Root> : null}
    {terminal ? <div className="mt-1.5 flex items-center gap-2"><Badge tone="neutral" shape="pill">
      {terminal.label}{terminal.date ? ` · ${formatDate(terminal.date)}` : ""}
    </Badge></div> : null}
  </div>;
}

function dateRange(step: StatusbarStep): string {
  return formatDateRange(step.startDate, step.endDate);
}

export const statusbarWidget = { edit: Statusbar, read: Statusbar, cell: Statusbar } satisfies WidgetDefinition<string>;
