import * as React from "react";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { Button, type ButtonProps } from "./button";
import { CountBadge } from "./badge";

export const selectionBarVariants = tv({
  slots: {
    root: "inline-flex h-10 max-w-full items-center gap-3 rounded-lg px-3 shadow-popover",
    count:
      "inline-flex shrink-0 items-center gap-1.5 text-13 font-semibold tabular-nums",
    countBadge: "",
    summary: "min-w-0 truncate text-13 font-medium",
    separator: "h-4 w-px shrink-0",
    actions: "flex shrink-0 items-center gap-1",
    action:
      "h-8 border-transparent px-2 text-13 focus-visible:focus-ring [&_.glyph]:size-4 [&_svg]:size-4",
  },
  variants: {
    surface: {
      brand: {
        root: "bg-brand text-on-brand",
        countBadge:
          "border-on-brand-divider bg-on-brand-soft-hover text-on-brand",
        summary: "text-on-brand",
        separator: "bg-on-brand-divider",
        action:
          "text-on-brand hover:bg-on-brand-soft-hover hover:text-on-brand",
      },
      sheet: {
        root: "border border-border-subtle bg-sheet text-fg",
        countBadge: "border-border bg-inset text-fg-muted",
        summary: "text-fg-muted",
        separator: "bg-border-subtle",
        action: "text-fg-2 hover:bg-inset hover:text-fg",
      },
    },
    position: {
      static: {},
      sticky: { root: "sticky top-3 z-bulk-bar" },
    },
    actionTone: {
      default: {},
      danger: {
        action: "hover:bg-danger-soft hover:text-danger-text",
      },
    },
  },
  defaultVariants: {
    surface: "brand",
    position: "static",
    actionTone: "default",
  },
});

type SelectionBarRecipeProps = VariantProps<typeof selectionBarVariants>;

export type SelectionBarSurface = NonNullable<
  SelectionBarRecipeProps["surface"]
>;
export type SelectionBarPosition = NonNullable<
  SelectionBarRecipeProps["position"]
>;
export type SelectionBarActionTone = NonNullable<
  SelectionBarRecipeProps["actionTone"]
>;

export type SelectionBarActionProps = Omit<
  ButtonProps,
  "active" | "className" | "size" | "variant"
> &
  Pick<SelectionBarRecipeProps, "surface"> & {
    className?: string;
    tone?: SelectionBarActionTone;
  };

export const SelectionBarAction = React.forwardRef<
  HTMLElement,
  SelectionBarActionProps
>(function SelectionBarAction(
  { surface = "brand", tone = "default", className, type = "button", ...props },
  ref,
) {
  const styles = selectionBarVariants({ actionTone: tone, surface });
  return (
    <Button
      ref={ref}
      type={type}
      variant="ghost"
      size="sm"
      className={styles.action({ className })}
      {...props}
    />
  );
});
SelectionBarAction.displayName = "SelectionBarAction";

export type SelectionBarProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className" | "color"
> &
  Pick<SelectionBarRecipeProps, "position" | "surface"> & {
    actions?: React.ReactNode;
    className?: string;
    clearLabel?: React.ReactNode;
    count: number;
    countLabel?: React.ReactNode;
    onClear?: () => void;
    summary?: React.ReactNode;
  };

export const SelectionBarRoot = React.forwardRef<
  HTMLDivElement,
  SelectionBarProps
>(function SelectionBarRoot(
  {
    actions,
    className,
    clearLabel,
    count,
    countLabel,
    onClear,
    position = "static",
    summary,
    surface = "brand",
    ...props
  },
  ref,
) {
  const t = useBaseT();
  const styles = selectionBarVariants({ position, surface });
  const hasSummary =
    summary !== null && summary !== undefined && summary !== false;
  const hasActions = Boolean(actions) || Boolean(onClear);

  return (
    <div
      ref={ref}
      className={styles.root({ className })}
      aria-live="polite"
      {...props}
    >
      <div className="flex min-w-0 items-center gap-3">
        <span className={styles.count()}>
          {countLabel ?? (
            <>
              <CountBadge
                value={count}
                size="sm"
                className={cn(styles.countBadge())}
              />
              <span>{t("selection.selected")}</span>
            </>
          )}
        </span>
        {hasSummary ? (
          <span className={styles.summary()}>{summary}</span>
        ) : null}
      </div>
      {hasActions ? <span className={styles.separator()} /> : null}
      {actions ? (
        <div className={styles.actions()}>{actions}</div>
      ) : onClear ? (
        <div className={styles.actions()}>
          <SelectionBarAction surface={surface} onClick={onClear}>
            {clearLabel ?? t("selection.clear")}
          </SelectionBarAction>
        </div>
      ) : null}
    </div>
  );
});
SelectionBarRoot.displayName = "SelectionBar";

export const SelectionBar = Object.assign(SelectionBarRoot, {
  Root: SelectionBarRoot,
  Action: SelectionBarAction,
});
