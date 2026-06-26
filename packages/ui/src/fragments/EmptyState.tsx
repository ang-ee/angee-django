import * as React from "react";

import { renderGlyph } from "../chrome/Glyph";
import { tv } from "../lib/variants";
import { Card } from "../ui/card";

export type EmptyStateProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className" | "title"
> & {
  actions?: React.ReactNode;
  className?: string;
  description?: React.ReactNode;
  /** Center the panel within a full-height parent (grid or flex) instead of
   *  sitting at its intrinsic `min-h-64`. Collapses the `grid place-content-center`
   *  wrapper hosts hand-rolled around a panel that should fill its container. */
  fill?: boolean;
  icon?: React.ReactNode | string;
  title: React.ReactNode;
};

export const emptyStateVariants = tv({
  slots: {
    // `flex-1` fills a flex parent, `min-h-full` a grid/block one — both inert in
    // the other layout, so one wrapper centers the panel in any full-height host.
    // (`min-h-full`, not `h-full`, so a flex-col sibling can't force an overflow.)
    fill: "grid min-h-full w-full flex-1 place-content-center",
    root: "grid min-h-64 place-content-center gap-3 p-8 text-center shadow-none",
    icon:
      "mx-auto grid size-12 place-content-center rounded-full bg-inset text-fg-muted [&_.glyph]:size-5 [&>svg]:size-5",
    copy: "space-y-1",
    title: "text-22 font-semibold text-fg",
    description: "mx-auto max-w-md text-sm leading-relaxed text-fg-muted",
    actions: "flex flex-wrap items-center justify-center gap-2",
  },
});

export const EmptyState = React.forwardRef<HTMLElement, EmptyStateProps>(
  function EmptyState(
    { actions, className, description, fill, icon, title, ...props },
    ref,
  ) {
    const styles = emptyStateVariants();
    const panel = (
      <Card
        ref={ref}
        className={styles.root({ className: fill ? undefined : className })}
        placeholder
        {...props}
      >
        {icon ? (
          <div className={styles.icon()}>{renderGlyph(icon)}</div>
        ) : null}
        <div className={styles.copy()}>
          <h2 className={styles.title()}>{title}</h2>
          {description ? (
            <p className={styles.description()}>{description}</p>
          ) : null}
        </div>
        {actions ? <div className={styles.actions()}>{actions}</div> : null}
      </Card>
    );
    if (!fill) return panel;
    return <div className={styles.fill({ className })}>{panel}</div>;
  },
);
EmptyState.displayName = "EmptyState";
