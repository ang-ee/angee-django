import * as React from "react";

import { renderGlyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { tv } from "../lib/variants";
import { PageHeader, type PageHeaderProps } from "../page";
import { Badge } from "../ui/badge";
import { textRoleVariants } from "../ui/text";

export const collectionHeaderVariants = tv({
  slots: {
    main: "min-w-0 flex-1 space-y-1",
    titleRow: "flex min-w-0 flex-wrap items-center gap-2",
    icon:
      "grid size-8 shrink-0 place-content-center rounded-6 bg-inset text-fg-2 [&_.glyph]:size-4 [&>svg]:size-4",
    title: "min-w-0 truncate text-lg font-semibold leading-tight text-fg",
    description: cn(textRoleVariants({ role: "description" }), "max-w-prose leading-relaxed"),
    actions: "flex shrink-0 flex-wrap items-center justify-end gap-2",
  },
});

type CollectionHeaderHeadingLevel = NonNullable<PageHeaderProps["headingLevel"]>;
type CollectionHeaderHeadingTag = `h${CollectionHeaderHeadingLevel}`;

export type CollectionHeaderProps = Omit<
  PageHeaderProps,
  "actions" | "children" | "crumbs" | "description" | "eyebrow" | "title"
> & {
  actions?: React.ReactNode;
  count?: React.ReactNode;
  description?: React.ReactNode;
  icon?: React.ReactNode | string;
  title: React.ReactNode;
};

export const CollectionHeader = React.forwardRef<
  HTMLElement,
  CollectionHeaderProps
>(function CollectionHeader(
  {
    actions,
    className,
    count,
    density = "comfortable",
    description,
    headingLevel = 1,
    icon,
    sticky = false,
    title,
    ...props
  },
  ref,
) {
  const styles = collectionHeaderVariants();
  const Heading = `h${headingLevel}` as CollectionHeaderHeadingTag;

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
          {icon ? (
            <span className={styles.icon()}>{renderGlyph(icon)}</span>
          ) : null}
          <Heading className={styles.title()}>{title}</Heading>
          {count !== undefined ? <Badge>{count}</Badge> : null}
        </div>
        {description ? (
          <p className={styles.description()}>{description}</p>
        ) : null}
      </div>
      {actions ? <div className={styles.actions()}>{actions}</div> : null}
    </PageHeader>
  );
});
CollectionHeader.displayName = "CollectionHeader";
