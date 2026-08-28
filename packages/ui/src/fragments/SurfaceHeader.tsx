import * as React from "react";

import { tv } from "../lib/variants";
import { PageHeader, type PageHeaderProps } from "../page";
import { Tag } from "../ui/badge";
import { IconTile } from "../ui/icon-tile";

export const surfaceHeaderVariants = tv({
  slots: {
    main: "min-w-0 flex-1",
    titleRow: "flex min-w-0 flex-wrap items-center gap-2",
    title: "min-w-0 truncate font-semibold leading-tight text-fg",
    subtitle: "text-fg-muted",
    actions: "flex shrink-0 flex-wrap items-center justify-end gap-2",
  },
  // Typography tracks `density` (which also drives PageHeader's padding/chrome),
  // so `density="compact"` is one coherent "compact pane header" switch: page
  // titling at `comfortable` (the default), pane-sized titling at `compact`.
  variants: {
    density: {
      comfortable: {
        title: "text-22",
        subtitle: "mt-1 max-w-prose text-13 leading-relaxed",
      },
      compact: {
        title: "text-15",
        subtitle: "truncate text-13",
      },
    },
  },
  defaultVariants: {
    density: "comfortable",
  },
});

type SurfaceHeaderHeadingLevel = NonNullable<PageHeaderProps["headingLevel"]>;
type SurfaceHeaderHeadingTag = `h${SurfaceHeaderHeadingLevel}`;

export type SurfaceHeaderProps = Omit<
  PageHeaderProps,
  "actions" | "children" | "crumbs" | "description" | "eyebrow" | "title"
> & {
  actions?: React.ReactNode;
  children?: React.ReactNode;
  fetching?: boolean;
  icon?: React.ReactNode | string;
  subtitle?: React.ReactNode;
  title: React.ReactNode;
};

export const SurfaceHeader = React.forwardRef<HTMLElement, SurfaceHeaderProps>(
  function SurfaceHeader(
    {
      actions,
      children,
      className,
      density = "comfortable",
      fetching = false,
      headingLevel = 1,
      icon,
      sticky = false,
      subtitle,
      title,
      ...props
    },
    ref,
  ) {
    const styles = surfaceHeaderVariants({ density });
    const Heading = `h${headingLevel}` as SurfaceHeaderHeadingTag;
    const headerActions = actions ?? children;

    return (
      <PageHeader
        ref={ref}
        className={className}
        density={density}
        sticky={sticky}
        {...props}
      >
        <div className={styles.main()}>
          <div className={styles.titleRow()}>
            {icon ? <IconTile icon={icon} size="lg" tone="brand" /> : null}
            <Heading className={styles.title()}>{title}</Heading>
            {fetching ? <Tag tone="info">Refreshing</Tag> : null}
          </div>
          {subtitle ? <p className={styles.subtitle()}>{subtitle}</p> : null}
        </div>
        {headerActions ? (
          <div className={styles.actions()}>{headerActions}</div>
        ) : null}
      </PageHeader>
    );
  },
);
SurfaceHeader.displayName = "SurfaceHeader";
