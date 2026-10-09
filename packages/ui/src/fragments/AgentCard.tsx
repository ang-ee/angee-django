import * as React from "react";

import { renderGlyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { toneClass, toneText, type Tone } from "../lib/tones";
import { tv } from "../lib/variants";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Spinner } from "../ui/spinner";

// ─── Types ───────────────────────────────────────────────────────────────────

export type AgentStatus = "running" | "idle" | "success" | "error" | "paused";

export interface AgentCardAction {
  label: string;
  icon?: React.ReactNode | string;
  onClick: () => void;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  disabled?: boolean;
}

export interface AgentCardProps
  extends Omit<React.HTMLAttributes<HTMLElement>, "className"> {
  /** Agent display name. */
  name: string;
  /** Short model or agent-type label, e.g. "claude-sonnet-4" or "Researcher". */
  model?: string;
  /** Icon glyph name or node. */
  icon?: React.ReactNode | string;
  /** Current run status. */
  status?: AgentStatus;
  /** One-line status message or current task description. */
  statusMessage?: string;
  /** Optional 0–100 progress. When provided renders the progress bar. */
  progress?: number;
  /** ISO timestamp the run started. */
  startedAt?: string | Date;
  /** Primary + secondary actions rendered in the card footer. */
  actions?: AgentCardAction[];
  /** Expandable log / output content slot. */
  children?: React.ReactNode;
  className?: string;
}

// ─── Status vocabulary ───────────────────────────────────────────────────────

const STATUS_TONE: Record<AgentStatus, Tone> = {
  running: "accent",
  idle:    "neutral",
  success: "success",
  error:   "danger",
  paused:  "warning",
};

const STATUS_LABEL: Record<AgentStatus, string> = {
  running: "Running",
  idle:    "Idle",
  success: "Done",
  error:   "Error",
  paused:  "Paused",
};

const STATUS_ICON: Record<AgentStatus, string> = {
  running: "zap",
  idle:    "minus",
  success: "circle-check",
  error:   "circle-x",
  paused:  "pause",
};

// ─── Variants ────────────────────────────────────────────────────────────────

const agentCardVariants = tv({
  slots: {
    root: [
      "group relative flex flex-col gap-0 rounded-8 border bg-sheet text-fg",
      "shadow-xs transition-shadow duration-200",
      "hover:shadow-sm",
    ],
    header: "flex items-start gap-3 px-4 pt-4 pb-3",
    iconWrap: [
      "mt-0.5 grid size-9 shrink-0 place-content-center rounded-7",
      "[&_.glyph]:size-4 [&>svg]:size-4",
    ],
    meta: "min-w-0 flex-1",
    title: "truncate text-sm font-semibold leading-snug text-fg",
    model: "mt-0.5 truncate text-2xs font-medium text-fg-muted",
    statusBadge: "ml-auto shrink-0 self-start",
    divider: "mx-4 h-px bg-border-subtle",
    body: "flex flex-col gap-2 px-4 py-3",
    statusRow: "flex items-center gap-1.5",
    statusDot: "size-1.5 shrink-0 rounded-full",
    statusMsg: "min-w-0 flex-1 truncate text-2xs text-fg-muted",
    progressWrap: "space-y-1",
    progressTrack: [
      "h-1 w-full overflow-hidden rounded-full bg-inset",
    ],
    progressFill: [
      "h-full rounded-full transition-[width] duration-500 ease-out",
    ],
    progressLabel: "flex items-center justify-between text-2xs text-fg-muted",
    footer: "flex items-center gap-2 px-4 pb-4 pt-2",
    footerMeta: "mr-auto text-2xs text-fg-subtle",
    childrenWrap: "px-4 pb-4",
    // Animated running indicator — a thin pulsing line at the top of the card.
    runningBar: [
      "pointer-events-none absolute inset-x-0 top-0 h-[2px] rounded-t-8 overflow-hidden",
    ],
    runningFill: [
      "h-full w-1/3 rounded-full",
      "motion-safe:animate-[agent-slide_1.8s_ease-in-out_infinite]",
    ],
  },
  variants: {
    status: {
      running: {
        root: "border-border",
      },
      idle: {
        root: "border-border-subtle",
      },
      success: {
        root: "border-border-subtle",
      },
      error: {
        root: "border-danger-line",
      },
      paused: {
        root: "border-border",
      },
    },
  },
  defaultVariants: {
    status: "idle",
  },
});

// ─── Helpers ─────────────────────────────────────────────────────────────────

function formatElapsed(start: string | Date): string {
  const ms = Date.now() - new Date(start).getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function progressTone(pct: number): Tone {
  if (pct >= 100) return "success";
  if (pct >= 60)  return "brand";
  if (pct >= 20)  return "accent";
  return "warning";
}

// ─── Pulsing progress bar (accent fill sliding across the track) ──────────────
// Uses only CSS — no JS timers — so it works inside Storybook without any hooks.
function RunningBar({ tone }: { tone: Tone }): React.ReactElement {
  const styles = agentCardVariants({ status: "running" });
  const textClass = toneText(tone);
  return (
    <span aria-hidden className={styles.runningBar()}>
      <span className={cn(styles.runningFill(), "text-current", textClass, "bg-current")} />
    </span>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export const AgentCard = React.forwardRef<HTMLElement, AgentCardProps>(
  function AgentCard(
    {
      name,
      model,
      icon = "cpu",
      status = "idle",
      statusMessage,
      progress,
      startedAt,
      actions,
      children,
      className,
      ...props
    },
    ref,
  ) {
    const styles = agentCardVariants({ status });
    const tone = STATUS_TONE[status];
    const isRunning = status === "running";
    const hasProgress = typeof progress === "number";
    const pct = hasProgress ? Math.min(100, Math.max(0, Math.round(progress))) : 0;
    const fillTone = hasProgress ? progressTone(pct) : tone;

    return (
      <article
        ref={ref as React.Ref<HTMLElement>}
        className={cn(styles.root(), className)}
        {...props}
      >
        {/* Animated running bar */}
        {isRunning && <RunningBar tone={tone} />}

        {/* ── Header ── */}
        <div className={styles.header()}>
          {/* Icon tile */}
          <span
            className={cn(
              styles.iconWrap(),
              toneClass(tone, "soft"),
            )}
          >
            {renderGlyph(icon)}
          </span>

          {/* Name + model */}
          <div className={styles.meta()}>
            <p className={styles.title()}>{name}</p>
            {model ? <p className={styles.model()}>{model}</p> : null}
          </div>

          {/* Status badge */}
          <Badge
            tone={tone}
            variant="soft"
            shape="pill"
            density="compact"
            className={styles.statusBadge()}
          >
            {isRunning ? (
              <Spinner size="sm" tone="current" aria-hidden />
            ) : (
              renderGlyph(STATUS_ICON[status])
            )}
            {STATUS_LABEL[status]}
          </Badge>
        </div>

        {/* ── Body (status message + progress) ── */}
        {(statusMessage || hasProgress) && (
          <>
            <span className={styles.divider()} />
            <div className={styles.body()}>
              {statusMessage && (
                <div className={styles.statusRow()}>
                  <span
                    className={cn(
                      styles.statusDot(),
                      toneClass(tone, "solid").split(" ")[0], // just bg-*
                    )}
                  />
                  <span className={styles.statusMsg()}>{statusMessage}</span>
                </div>
              )}

              {hasProgress && (
                <div className={styles.progressWrap()}>
                  <div className={styles.progressLabel()}>
                    <span>Progress</span>
                    <span className="tabular-nums">{pct}%</span>
                  </div>
                  <span className={styles.progressTrack()}>
                    <span
                      className={cn(
                        styles.progressFill(),
                        toneClass(fillTone, "solid").split(" ")[0],
                      )}
                      style={{ width: `${pct}%` }}
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={pct}
                      aria-label={`Progress: ${pct}%`}
                    />
                  </span>
                </div>
              )}
            </div>
          </>
        )}

        {/* ── Expandable children slot (log output etc.) ── */}
        {children ? (
          <>
            <span className={styles.divider()} />
            <div className={styles.childrenWrap()}>{children}</div>
          </>
        ) : null}

        {/* ── Footer (actions + elapsed) ── */}
        {(actions?.length || startedAt) ? (
          <>
            <span className={styles.divider()} />
            <div className={styles.footer()}>
              {startedAt && (
                <ElapsedTime startedAt={startedAt} running={isRunning} />
              )}
              {actions?.map((action, i) => (
                <Button
                  key={i}
                  variant={action.variant ?? (i === 0 ? "ghost" : "ghost")}
                  size="sm"
                  disabled={action.disabled}
                  onClick={action.onClick}
                >
                  {action.icon ? renderGlyph(action.icon) : null}
                  {action.label}
                </Button>
              ))}
            </div>
          </>
        ) : null}
      </article>
    );
  },
);

AgentCard.displayName = "AgentCard";

// ─── Elapsed time sub-component ──────────────────────────────────────────────
// Uses a 1s interval only while the agent is actively running.

function ElapsedTime({
  startedAt,
  running,
}: {
  startedAt: string | Date;
  running: boolean;
}): React.ReactElement {
  const [, tick] = React.useReducer((n: number) => n + 1, 0);

  React.useEffect(() => {
    if (!running) return;
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [running]);

  const styles = agentCardVariants();
  return (
    <span className={styles.footerMeta()}>
      {formatElapsed(startedAt)}
    </span>
  );
}
