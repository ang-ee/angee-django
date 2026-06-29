import * as React from "react";

import { tv } from "../lib/variants";
import { SectionEyebrow } from "../ui/section-eyebrow";

export interface MetaGridItem {
  action?: React.ReactNode;
  id?: string;
  label: React.ReactNode;
  value?: React.ReactNode;
}

export type MetaGridRow = MetaGridItem | readonly [React.ReactNode, React.ReactNode];

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
};

export const metaGridVariants = tv({
  slots: {
    grid:
      "grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-13",
    row: "contents",
    label: "normal-case tracking-normal",
    value: "m-0 min-w-0 break-words text-fg",
    action: "ml-2 inline-flex align-middle",
    section: "space-y-2",
  },
});

export const MetaGrid = React.forwardRef<HTMLDListElement, MetaGridProps>(
  function MetaGrid({ className, emptyValue = "-", rows, ...props }, ref) {
    const styles = metaGridVariants();

    return (
      <dl ref={ref} className={styles.grid({ className })} {...props}>
        {rows.map((row, index) => {
          const item = normalizeMetaGridRow(row);
          return (
            <div key={item.id ?? index} className={styles.row()}>
              <SectionEyebrow
                as="dt"
                className={styles.label()}
                tracking="normal"
                weight="medium"
              >
                {item.label}
              </SectionEyebrow>
              <dd className={styles.value()}>
                <span>{item.value ?? emptyValue}</span>
                {item.action ? (
                  <span className={styles.action()}>{item.action}</span>
                ) : null}
              </dd>
            </div>
          );
        })}
      </dl>
    );
  },
);
MetaGrid.displayName = "MetaGrid";

export const MetaSection = React.forwardRef<HTMLElement, MetaSectionProps>(
  function MetaSection({ children, className, title, ...props }, ref) {
    const styles = metaGridVariants();

    return (
      <section ref={ref} className={styles.section({ className })} {...props}>
        <SectionEyebrow as="h3">{title}</SectionEyebrow>
        {children}
      </section>
    );
  },
);
MetaSection.displayName = "MetaSection";

function normalizeMetaGridRow(row: MetaGridRow): MetaGridItem {
  if (isMetaGridTuple(row)) {
    return { label: row[0], value: row[1] };
  }
  return row;
}

function isMetaGridTuple(
  row: MetaGridRow,
): row is readonly [React.ReactNode, React.ReactNode] {
  return Array.isArray(row);
}
