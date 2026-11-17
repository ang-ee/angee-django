import { diffWords } from "diff";
import * as React from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { toneClass } from "../lib/tones";
import { tv } from "../lib/variants";

/**
 * @kind token
 */
export const textDiffVariants = tv({
  slots: {
    root: "whitespace-pre-wrap break-words",
    removed: cn(toneClass("danger", "soft"), "rounded-2 line-through"),
    added: cn(toneClass("success", "soft"), "rounded-2 underline underline-offset-2"),
  },
});

export type TextDiffProps = Omit<React.HTMLAttributes<HTMLDivElement>, "children" | "className"> & {
  className?: string;
  /** The earlier text. */
  before: string;
  /** The later text. */
  after: string;
  /**
   * `before` renders the earlier text with its removals struck through, `after` the
   * later text with its additions underlined; unset renders both inline. Two
   * instances, one per side, fill a before/after pair such as `ComparisonRows` cells,
   * each with its own `lang`.
   */
  side?: "before" | "after";
};

/**
 * A word-level difference between two texts, marked with native `<del>` and `<ins>`.
 * jsdiff's `diffWords` owns the comparison; this owns the marks and a summary of the
 * changes that names the group for assistive technology.
 * @kind fragment
 */
export function TextDiff({ after, before, className, side, ...props }: TextDiffProps): React.ReactElement {
  const t = useUiT();
  const styles = textDiffVariants();
  const changes = React.useMemo(() => diffWords(before, after), [before, after]);
  const removals = side === "after" ? 0 : changes.filter((change) => change.removed).length;
  const additions = side === "before" ? 0 : changes.filter((change) => change.added).length;
  const counted = [
    ...(removals ? [t("textDiff.removals", { count: removals })] : []),
    ...(additions ? [t("textDiff.additions", { count: additions })] : []),
  ];
  const summary = counted.length === 2
    ? t("textDiff.summary", { removals: counted[0], additions: counted[1] })
    : counted[0] ?? t("textDiff.unchanged");
  return (
    <div role="group" aria-label={summary} className={styles.root({ className })} {...props}>
      {changes.map((change, index) => {
        if (change.removed) return side === "after" ? null : <del key={index} className={styles.removed()}>{change.value}</del>;
        if (change.added) return side === "before" ? null : <ins key={index} className={styles.added()}>{change.value}</ins>;
        return <React.Fragment key={index}>{change.value}</React.Fragment>;
      })}
    </div>
  );
}
