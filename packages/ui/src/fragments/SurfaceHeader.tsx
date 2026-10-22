import * as React from "react";

import { tv } from "../lib/variants";
import { PageHeader, type PageHeaderProps } from "../page";
import { Chip } from "../ui/chip";
import { IconTile } from "../ui/icon-tile";
import { textRoleVariants } from "../ui/text";

/**
 * @kind token
 */
export const surfaceHeaderVariants = tv({
  slots: {
    main: "min-w-0 flex-1",
    titleRow: "flex min-w-0 flex-wrap items-center gap-2",
    title: "min-w-0 truncate leading-tight",
    subtitle: "text-fg-muted",
    actions: "flex shrink-0 flex-wrap items-center justify-end gap-2",
  },
  // Typography tracks `density` (which also drives PageHeader's padding/chrome),
  // so `density="compact"` is one coherent "compact pane header" switch: page
  // titling at `comfortable` (the default), pane-sized titling at `compact`.
  variants: {
    density: {
      comfortable: {
        title: textRoleVariants({ role: "display" }),
        subtitle: "mt-1 max-w-prose text-13 leading-relaxed",
      },
      compact: {
        title: textRoleVariants({ role: "title" }),
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
  "actions" | "children" | "crumbs" | "description" | "eyebrow" | "headingLevel" | "title"
> & {
  actions?: React.ReactNode;
  children?: React.ReactNode;
  fetching?: boolean;
  icon?: React.ReactNode | string;
  /** null when the title is a labelled editing control rather than a heading. */
  headingLevel?: SurfaceHeaderHeadingLevel | null;
  subtitle?: React.ReactNode;
  title: React.ReactNode;
};

/**
 * @kind region
 * @region record-header
 */
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
    const Heading = headingLevel === null ? "div" : `h${headingLevel}` as SurfaceHeaderHeadingTag;
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
            {fetching ? <Chip tone="info">Refreshing</Chip> : null}
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
