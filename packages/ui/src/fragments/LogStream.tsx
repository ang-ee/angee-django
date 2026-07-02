import * as React from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { tv } from "../lib/variants";

export const logStreamVariants = tv({
  slots: {
    root: "flex min-h-0 flex-col overflow-hidden rounded-6 border border-border-subtle bg-inset",
    viewport: "min-h-0 flex-1 overflow-auto p-3 font-mono text-xs leading-5 text-fg",
    line: "whitespace-pre-wrap break-all",
    empty: "text-fg-muted",
  },
});

export interface LogStreamProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, "children"> {
  /** The accumulated log lines, oldest first. */
  lines: readonly string[];
  /** Shown when no lines have arrived yet. */
  emptyContent?: React.ReactNode;
  className?: string;
  /** Distance in px from the bottom within which the view keeps following. */
  followThreshold?: number;
}

/**
 * A scrollable live-log viewport that follows the tail as lines stream in and
 * stops following once the reader scrolls up (and resumes when they return to
 * the bottom). The owner of "tail a growing line buffer" — compose it over any
 * subscription that accumulates string lines rather than hand-rolling a scroll
 * box per consumer.
 */
export const LogStream = React.forwardRef<HTMLDivElement, LogStreamProps>(
  function LogStream(
    { lines, emptyContent, className, followThreshold = 24, ...props },
    forwardedRef,
  ) {
    const t = useUiT();
    const styles = logStreamVariants();
    const viewportRef = React.useRef<HTMLDivElement | null>(null);
    // Whether the tail is "stuck" to the bottom; a reader scrolling up detaches it.
    const followingRef = React.useRef(true);

    const handleScroll = React.useCallback(() => {
      const el = viewportRef.current;
      if (!el) return;
      followingRef.current =
        el.scrollHeight - el.scrollTop - el.clientHeight <= followThreshold;
    }, [followThreshold]);

    React.useLayoutEffect(() => {
      const el = viewportRef.current;
      if (!el || !followingRef.current) return;
      el.scrollTop = el.scrollHeight;
    }, [lines]);

    return (
      <div ref={forwardedRef} className={cn(styles.root(), className)} {...props}>
        <div ref={viewportRef} className={styles.viewport()} onScroll={handleScroll}>
          {lines.length === 0 ? (
            <span className={styles.empty()}>
              {emptyContent ?? t("logStream.waiting")}
            </span>
          ) : (
            lines.map((line, index) => (
              <div key={index} className={styles.line()}>
                {line || " "}
              </div>
            ))
          )}
        </div>
      </div>
    );
  },
);
LogStream.displayName = "LogStream";
