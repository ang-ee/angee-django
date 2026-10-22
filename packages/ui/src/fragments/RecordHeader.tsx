import * as React from "react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { type Tone } from "../lib/tones";
import { PageHeader, type PageHeaderProps } from "../page";
import { Chip } from "../ui/chip";
import { IconTile } from "../ui/icon-tile";
import { textRoleVariants } from "../ui/text";

/**
 * @kind token
 */
export const recordHeaderVariants = tv({
  slots: {
    main: "min-w-0 flex-1 space-y-1",
    crumbs: textRoleVariants({ role: "caption" }),
    titleRow: "flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1",
    // The title keeps a readable width; its adornments and status wrap below it.
    title: cn(textRoleVariants({ role: "display" }), "min-w-0 flex-[1_1_16rem] leading-tight [overflow-wrap:anywhere]"),
    meta: cn(textRoleVariants({ role: "meta" }), "flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1"),
    description: cn(textRoleVariants({ role: "description" }), "max-w-prose leading-relaxed"),
    actions: "flex shrink-0 flex-wrap items-center justify-end gap-2",
  },
  variants: {
    // A compact header (a sticky band) titles in the title role; the page header in display.
    density: {
      compact: { title: textRoleVariants({ role: "title" }) },
      comfortable: { title: "" },
    },
  },
});

type RecordHeaderRecipeProps = VariantProps<typeof recordHeaderVariants>;
type RecordHeaderHeadingLevel = NonNullable<PageHeaderProps["headingLevel"]>;
type RecordHeaderHeadingTag = `h${RecordHeaderHeadingLevel}` | "div";

export interface RecordHeaderStatus {
  label: React.ReactNode;
  tone?: Tone;
}

export type RecordHeaderProps = Omit<
  PageHeaderProps,
  "actions" | "children" | "crumbs" | "description" | "eyebrow" | "headingLevel" | "title"
> &
  Pick<RecordHeaderRecipeProps, never> & {
    /** The title's heading level; `null` while the title is an editable field. */
    headingLevel?: RecordHeaderHeadingLevel | null;
    /** A full-width row above the title: a status control that does not fit the title row. */
    lead?: React.ReactNode;
    /** A full-width row after the facts: compact read-only extras the page adds to the header. */
    extra?: React.ReactNode;
    /** Marks and title-placed fields, after the title on its row. */
    titleAdornments?: React.ReactNode;
    actions?: React.ReactNode;
    crumbs?: React.ReactNode;
    description?: React.ReactNode;
    icon?: React.ReactNode | string;
    meta?: React.ReactNode;
    /** A lifecycle badge, or the record's own status control (a statusbar). */
    status?: RecordHeaderStatus | React.ReactElement;
    title: React.ReactNode;
    type?: React.ReactNode;
  };

/**
 * @kind region
 * @region record-header
 */
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
      lead,
      extra,
      meta,
      status,
      sticky = false,
      title,
      titleAdornments,
      type,
      ...props
    },
    ref,
  ) {
    const styles = recordHeaderVariants({ density });
    // `headingLevel: null` renders the title row without a heading: an editable title is a field.
    const Heading = (headingLevel === null ? "div" : `h${headingLevel}`) as RecordHeaderHeadingTag;

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
          {lead ? <div>{lead}</div> : null}
          <div className={styles.titleRow()} data-record-title-row="true">
            {icon ? <IconTile icon={icon} size="lg" tone="brand" /> : null}
            <Heading className={styles.title()}>{title}</Heading>
            {titleAdornments}
            {type ? <Chip>{type}</Chip> : null}
            {status ? (
              React.isValidElement(status)
                ? <div className="ml-auto flex min-w-0 flex-auto justify-end">{status}</div>
                : <Chip tone={status.tone ?? "neutral"}>{status.label}</Chip>
            ) : null}
          </div>
          {meta ? <div className={styles.meta()}>{meta}</div> : null}
          {description ? (
            <p className={styles.description()}>{description}</p>
          ) : null}
          {extra ? <div className="pt-1">{extra}</div> : null}
        </div>
        {actions ? <div className={styles.actions()}>{actions}</div> : null}
      </PageHeader>
    );
  },
);
RecordHeader.displayName = "RecordHeader";
