import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";

export const pageHeaderVariants = tv({
  slots: {
    root:
      "flex shrink-0 items-start justify-between gap-4 border-b border-border-subtle bg-sheet",
    main: "min-w-0 flex-1",
    crumbs: "mb-1 flex min-w-0 items-center gap-1 text-13 text-fg-muted",
    eyebrow: "mb-1 text-2xs font-semibold uppercase text-fg-muted",
    title: "truncate text-15 font-semibold leading-6 text-fg",
    description: "mt-1 max-w-prose text-13 leading-5 text-fg-2",
    actions: "flex shrink-0 flex-wrap items-center justify-end gap-2",
  },
  variants: {
    sticky: {
      true: { root: "sticky top-0 z-sticky-cell" },
      false: { root: "" },
    },
    density: {
      compact: { root: "px-4 py-2.5" },
      comfortable: { root: "px-5 py-3" },
    },
  },
  defaultVariants: {
    sticky: false,
    density: "comfortable",
  },
});

type PageHeaderRecipeProps = VariantProps<typeof pageHeaderVariants>;
type PageHeaderHeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;
type PageHeaderHeadingTag = `h${PageHeaderHeadingLevel}`;

export type PageHeaderProps = React.HTMLAttributes<HTMLElement> &
  PageHeaderRecipeProps & {
    actions?: React.ReactNode;
    children?: React.ReactNode;
    className?: string;
    crumbs?: React.ReactNode;
    description?: React.ReactNode;
    eyebrow?: React.ReactNode;
    headingLevel?: PageHeaderHeadingLevel;
    title?: React.ReactNode;
  };

export const PageHeader = React.forwardRef<HTMLElement, PageHeaderProps>(
  function PageHeader(
    {
      actions,
      children,
      className,
      crumbs,
      density = "comfortable",
      description,
      eyebrow,
      headingLevel = 1,
      sticky = false,
      title,
      ...props
    },
    ref,
  ) {
    const styles = pageHeaderVariants({ density, sticky });
    const Heading = `h${headingLevel}` as PageHeaderHeadingTag;

    return (
      <header ref={ref} className={styles.root({ className })} {...props}>
        {children ?? (
          <>
            <div className={styles.main()}>
              {crumbs ? <div className={styles.crumbs()}>{crumbs}</div> : null}
              {eyebrow ? (
                <div className={styles.eyebrow()}>{eyebrow}</div>
              ) : null}
              {title ? <Heading className={styles.title()}>{title}</Heading> : null}
              {description ? (
                <div className={styles.description()}>{description}</div>
              ) : null}
            </div>
            {actions ? <div className={styles.actions()}>{actions}</div> : null}
          </>
        )}
      </header>
    );
  },
);
PageHeader.displayName = "PageHeader";
