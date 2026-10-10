import * as React from "react";

import { tv } from "../lib/variants";
import { SectionEyebrow } from "../ui/section-eyebrow";
import {
  DefinitionPair,
  definitionFromTuple,
  type DefinitionPairTuple,
  type DefinitionPairValue,
} from "./DefinitionPair";

export interface MetaGridItem extends DefinitionPairValue {
  id?: string;
}

export type MetaGridRow = MetaGridItem | DefinitionPairTuple;

export type MetaGridProps = Omit<
  React.HTMLAttributes<HTMLDListElement>,
  "className"
> & {
  className?: string;
  emptyValue?: React.ReactNode;
  rows: readonly MetaGridRow[];
};

export type MetaSectionProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className" | "title"
> & {
  className?: string;
  title: React.ReactNode;
  /** Semantic depth within the containing page; presentation remains unchanged. */
  headingLevel?: 2 | 3 | 4 | 5 | 6;
};

export const metaGridVariants = tv({
  slots: {
    grid:
      "grid min-w-0 grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-13",
    section: "space-y-2",
  },
});

export const MetaGrid = React.forwardRef<HTMLDListElement, MetaGridProps>(
  function MetaGrid({ className, emptyValue = "-", rows, ...props }, ref) {
    const styles = metaGridVariants();

    return (
      <dl ref={ref} className={styles.grid({ className })} {...props}>
        {rows.map((row, index) => {
          const item: DefinitionPairValue = definitionFromTuple(row);
          const key = "id" in row && row.id != null ? row.id : index;
          return (
            <DefinitionPair
              key={key}
              action={item.action}
              density="compact"
              emptyValue={emptyValue}
              label={item.label}
              layout="contents"
              orientation="inline"
              value={item.value}
            />
          );
        })}
      </dl>
    );
  },
);
MetaGrid.displayName = "MetaGrid";

export const MetaSection = React.forwardRef<HTMLElement, MetaSectionProps>(
  function MetaSection({ children, className, title, headingLevel = 3, ...props }, ref) {
    const styles = metaGridVariants();

    return (
      <section ref={ref} className={styles.section({ className })} {...props}>
        <SectionEyebrow as={`h${headingLevel}`}>{title}</SectionEyebrow>
        {children}
      </section>
    );
  },
);
MetaSection.displayName = "MetaSection";
