import * as React from "react";

import { renderGlyph } from "../chrome/Glyph";
import { tv, type VariantProps } from "../lib/variants";
import { type Tone } from "../lib/tones";
import { PageHeader, type PageHeaderProps } from "../page";
import { Badge } from "../ui/badge";

export const recordHeaderVariants = tv({
  slots: {
    main: "min-w-0 flex-1 space-y-1",
    crumbs: "text-2xs leading-4 text-fg-muted",
    titleRow: "flex min-w-0 flex-wrap items-center gap-2",
    icon:
      "grid size-8 shrink-0 place-content-center rounded-md bg-brand-soft text-brand-soft-text [&_.glyph]:size-4 [&>svg]:size-4",
    title: "min-w-0 truncate text-lg font-semibold leading-tight text-fg",
    meta: "flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-13 text-fg-muted",
    description: "max-w-prose text-13 leading-relaxed text-fg-2",
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
            {icon ? (
              <span className={styles.icon()}>{renderGlyph(icon)}</span>
            ) : null}
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
