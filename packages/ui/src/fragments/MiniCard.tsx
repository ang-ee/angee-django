import * as React from "react";

import { renderGlyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { type Tone } from "../lib/tones";
import { tv } from "../lib/variants";
import { Tag } from "../ui/badge";
import { Card } from "../ui/card";

export interface MiniCardPrimaryTag {
  label: React.ReactNode;
  tone: Tone;
}

export type MiniCardProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className" | "title"
> & {
  className?: string;
  icon?: React.ReactNode | string;
  meta?: React.ReactNode;
  primaryTag?: MiniCardPrimaryTag;
  tags?: React.ReactNode;
  title: React.ReactNode;
};

export const miniCardVariants = tv({
  slots: {
    root: "bg-canvas px-3 py-2 shadow-none",
    header: "flex min-w-0 items-start justify-between gap-2",
    titleWrap: "min-w-0 flex-1",
    titleRow: "flex min-w-0 items-center gap-2",
    icon:
      "grid size-6 shrink-0 place-content-center rounded-md bg-inset text-fg-2 [&_.glyph]:size-3.5 [&>svg]:size-3.5",
    title: "truncate text-13 font-medium text-fg",
    meta: "mt-0.5 truncate text-2xs text-fg-muted",
    tags: "mt-2 flex flex-wrap gap-1",
  },
});

export const MiniCard = React.forwardRef<HTMLElement, MiniCardProps>(
  function MiniCard(
    { className, icon, meta, primaryTag, tags, title, ...props },
    ref,
  ) {
    const styles = miniCardVariants();

    return (
      <Card
        ref={ref}
        className={cn(styles.root(), className)}
        density="sm"
        {...props}
      >
        <div className={styles.header()}>
          <div className={styles.titleWrap()}>
            <div className={styles.titleRow()}>
              {icon ? (
                <span className={styles.icon()}>{renderGlyph(icon)}</span>
              ) : null}
              <p className={styles.title()}>{title}</p>
            </div>
            {meta ? <p className={styles.meta()}>{meta}</p> : null}
          </div>
          {primaryTag ? (
            <Tag tone={primaryTag.tone}>{primaryTag.label}</Tag>
          ) : null}
        </div>
        {tags ? <div className={styles.tags()}>{tags}</div> : null}
      </Card>
    );
  },
);
MiniCard.displayName = "MiniCard";
