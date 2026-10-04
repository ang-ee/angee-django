import type { ReactElement, ReactNode } from "react";
import { cn } from "../lib/cn";
import { StatusIcon } from "../ui/status-icon";
import { RelativeTime } from "../fragments/RelativeTime";
import { toneText, type Tone } from "../lib/tones";

export interface StepListItem {
  id: string;
  state: "trigger" | "done" | "current" | "planned" | "optional" | "stopped";
  title: ReactNode;
  outcome?: ReactNode;
  tone?: Tone;
  detail?: ReactNode;
  timestamp?: string | Date | null;
  children?: ReactNode;
}

/** Vertical steps with muted future paths and a connector to the next entry. */
export function StepList({ items }: { items: readonly StepListItem[] }): ReactElement {
  return <ol className="min-w-0">{items.map((item, index) => {
    const planned = item.state === "planned" || item.state === "optional";
    const tone = planned || item.state === "stopped" ? "muted" : item.tone === "danger" ? "danger"
      : item.state === "current" ? "warning" : item.state === "trigger" ? "info" : "success";
    return <li key={item.id} className="grid min-w-0 gap-x-3" style={{ gridTemplateColumns: "1.25rem minmax(0,1fr)" }}>
      <div className="flex flex-col items-center" aria-hidden>
        <StatusIcon tone={tone} icon={item.state === "stopped" ? "stop" : planned ? "circle" : item.state === "current" ? "help" : item.state === "trigger" ? "activity" : "check"} />
        {index < items.length - 1 ? <span className={cn("my-1 flex-1", planned ? "border-l border-dashed border-border-strong" : "w-px bg-border")} /> : null}
      </div>
      <div className={cn("min-w-0", index === items.length - 1 ? "pb-1" : "pb-5")}>
        <div className="flex min-w-0 items-start justify-between gap-2">
          <p className={cn("min-w-0 text-13 leading-5 [overflow-wrap:anywhere]", planned ? "font-medium text-fg-muted" : "font-semibold text-fg")}>{item.title}</p>
          {item.timestamp ? <RelativeTime value={item.timestamp} className="shrink-0 pt-0.5 text-xs text-fg-muted" /> : null}
        </div>
        {item.outcome ? <p className={cn("text-13 font-medium leading-5", toneText(item.tone ?? "success"))}>{item.outcome}</p> : null}
        {item.detail ? <div className="mt-0.5 text-13 text-fg-2">{item.detail}</div> : null}
        {item.children ? <div className="mt-2 grid min-w-0 gap-2">{item.children}</div> : null}
      </div>
    </li>;
  })}</ol>;
}
