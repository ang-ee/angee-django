import * as React from "react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { type Tone } from "../lib/tones";
import { PageHeader, type PageHeaderProps } from "../page";
import { Badge } from "../ui/badge";
import { IconTile } from "../ui/icon-tile";
import { textRoleVariants } from "../ui/text";

export const recordHeaderVariants = tv({
  slots: {
    main: "min-w-0 flex-1 space-y-1",
    crumbs: textRoleVariants({ role: "caption" }),
    titleRow: "flex min-w-0 flex-wrap items-center gap-2",
    title: "min-w-0 truncate text-lg font-semibold leading-tight text-fg",
    meta: cn(textRoleVariants({ role: "meta" }), "flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1"),
    description: cn(textRoleVariants({ role: "description" }), "max-w-prose leading-relaxed"),
    actions: "flex shrink-0 flex-wrap items-center justify-end gap-2",
  },
});

type RecordHeaderRecipeProps = VariantProps<typeof recordHeaderVariants>;
type RecordHeaderHeadingLevel = NonNullable<PageHeaderProps["headingLevel"]>;
type RecordHeaderHeadingTag = `h${RecordHeaderHeadingLevel}`;

export interface RecordHeaderStatus {
  label: React.ReactNode;
  tone?: Tone;
}

export type RecordHeaderProps = Omit<
  PageHeaderProps,
  "actions" | "children" | "crumbs" | "description" | "eyebrow" | "title"
> &
  Pick<RecordHeaderRecipeProps, never> & {
    actions?: React.ReactNode;
    crumbs?: React.ReactNode;
    description?: React.ReactNode;
    icon?: React.ReactNode | string;
    meta?: React.ReactNode;
    status?: RecordHeaderStatus;
    title: React.ReactNode;
    type?: React.ReactNode;
  };

export const RecordHeader = React.forwardRef<HTMLElement, RecordHeaderProps>(
  function RecordHeader(
    {
      actions,
      className,
      crumbs,
      density = "comfortable",
      description,
      headingLevel = 1,
      icon,
      meta,
      status,
      sticky = false,
      title,
      type,
      ...props
    },
    ref,
  ) {
    const styles = recordHeaderVariants();
    const Heading = `h${headingLevel}` as RecordHeaderHeadingTag;

    return (
      <PageHeader
        ref={ref}
        className={className}
        density={density}
        sticky={sticky}
        {...props}
      >
        <div className={styles.main()}>
          {crumbs ? <div className={styles.crumbs()}>{crumbs}</div> : null}
          <div className={styles.titleRow()}>
            {icon ? <IconTile icon={icon} size="lg" tone="brand" /> : null}
            <Heading className={styles.title()}>{title}</Heading>
            {type ? <Badge>{type}</Badge> : null}
            {status ? (
              <Badge tone={status.tone ?? "neutral"}>{status.label}</Badge>
            ) : null}
          </div>
          {meta ? <div className={styles.meta()}>{meta}</div> : null}
          {description ? (
            <p className={styles.description()}>{description}</p>
          ) : null}
        </div>
        {actions ? <div className={styles.actions()}>{actions}</div> : null}
      </PageHeader>
    );
  },
);
RecordHeader.displayName = "RecordHeader";
