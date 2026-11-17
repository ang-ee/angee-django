import { diffWords } from "diff";
import * as React from "react";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { toneClass } from "../lib/tones";
import { tv } from "../lib/variants";

/** One run of a word comparison: unchanged, removed from the earlier text or added in the later one. */
export interface TextChange {
  value: string;
  added?: boolean;
  removed?: boolean;
}

/** Two texts compared once, for one `TextDiff` or for both sides of a before/after pair. */
export interface TextComparison {
  before: string;
  after: string;
  /** The word runs, or null when the texts were too different to compare within the time budget. */
  changes: readonly TextChange[] | null;
}

/** jsdiff abandons a comparison after this long, so a render never stalls on unrelated texts. */
const COMPARE_TIMEOUT_MS = 100;

/**
 * Compare two texts word by word. jsdiff's `diffWords` owns the comparison; texts
 * too different to compare within the time budget come back with `changes: null`,
 * which `TextDiff` shows as a whole-text replacement.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function compareText(before: string, after: string): TextComparison {
  return { before, after, changes: diffWords(before, after, { timeout: COMPARE_TIMEOUT_MS }) ?? null };
}

/**
 * @kind token
 */
export const textDiffVariants = tv({
  slots: {
    root: "whitespace-pre-wrap break-words",
    note: "mb-1 block whitespace-normal text-2xs text-fg-muted",
    removed: cn(toneClass("danger", "soft"), "rounded-2 line-through"),
    added: cn(toneClass("success", "soft"), "rounded-2 underline underline-offset-2"),
  },
});

export type TextDiffProps = Omit<React.HTMLAttributes<HTMLDivElement>, "children" | "className"> & {
  className?: string;
  /** The comparison to show; compute it once with `compareText` and share it between sides. */
  comparison: TextComparison;
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
 * Each mark carries a visually hidden "removed"/"added" cue, and a summary of the
 * changes names the group for assistive technology.
 * @kind fragment
 */
export function TextDiff({ className, comparison, side, ...props }: TextDiffProps): React.ReactElement {
  const t = useUiT();
  const styles = textDiffVariants();
  const replaced = comparison.changes === null;
  const changes: readonly TextChange[] = comparison.changes ?? [
    ...(comparison.before ? [{ value: comparison.before, removed: true }] : []),
    ...(comparison.after ? [{ value: comparison.after, added: true }] : []),
  ];
  const removals = side === "after" ? 0 : changes.filter((change) => change.removed).length;
  const additions = side === "before" ? 0 : changes.filter((change) => change.added).length;
  const removalsText = removals ? t("textDiff.removals", { count: removals }) : null;
  const additionsText = additions ? t("textDiff.additions", { count: additions }) : null;
  const summary = replaced
    ? t("textDiff.replaced")
    : removalsText && additionsText
      ? t("textDiff.summary", { removals: removalsText, additions: additionsText })
      : removalsText ?? additionsText ?? t("textDiff.unchanged");
  return (
    <div role="group" aria-label={summary} className={styles.root({ className })} {...props}>
      {replaced ? <span className={styles.note()}>{t("textDiff.replaced")}</span> : null}
      {changes.map((change, index) => {
        if (change.removed) {
          return side === "after" ? null : (
            <del key={index} className={styles.removed()}>
              <span className="sr-only">{t("textDiff.removed")} </span>{change.value}
            </del>
          );
        }
        if (change.added) {
          return side === "before" ? null : (
            <ins key={index} className={styles.added()}>
              <span className="sr-only">{t("textDiff.added")} </span>{change.value}
            </ins>
          );
        }
        return <React.Fragment key={index}>{change.value}</React.Fragment>;
      })}
    </div>
  );
}
