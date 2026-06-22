import * as React from "react";

import { tv } from "../lib/variants";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  type CardDensity,
} from "../ui/card";
import { EmptyState, type EmptyStateProps } from "./EmptyState";
import { LoadingPanel } from "./LoadingPanel";
import { MetaGrid, type MetaGridProps } from "./MetaGrid";
import { MetricStrip, type MetricTileValue } from "./MetricStrip";
import { RecordHeader, type RecordHeaderProps } from "./RecordHeader";

export type DetailSurfaceEmptyState = Pick<
  EmptyStateProps,
  "actions" | "description" | "icon" | "title"
>;

export type DetailSurfaceProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className" | "title"
> &
  Pick<
    RecordHeaderProps,
    "actions" | "description" | "icon" | "meta" | "status" | "title" | "type"
  > & {
    children?: React.ReactNode;
    className?: string;
    empty?: DetailSurfaceEmptyState | null | false;
    loading?: boolean;
    loadingMessage?: string;
    metrics?: readonly MetricTileValue[];
  };

export type DetailSectionProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className" | "title"
> & {
  children?: React.ReactNode;
  className?: string;
  density?: CardDensity;
  rows?: MetaGridProps["rows"];
  title: React.ReactNode;
};

export const detailSurfaceVariants = tv({
  slots: {
    root: "flex min-h-0 flex-col gap-4 p-4",
    section: "shadow-none",
  },
});

/**
 * Page-level detail chrome for records, daemon objects, and metadata nodes. The
 * surface owns only state framing and vertical rhythm; callers supply every
 * domain fact, action, metric, and section row.
 */
export const DetailSurface = React.forwardRef<HTMLElement, DetailSurfaceProps>(
  function DetailSurface(
    {
      actions,
      children,
      className,
      description,
      empty,
      icon,
      loading = false,
      loadingMessage,
      meta,
      metrics,
      status,
      title,
      type,
      ...props
    },
    ref,
  ) {
    const styles = detailSurfaceVariants();

    if (loading) {
      return <LoadingPanel message={loadingMessage} />;
    }
    if (empty) {
      return <EmptyState fill {...empty} />;
    }

    return (
      <section ref={ref} className={styles.root({ className })} {...props}>
        <RecordHeader
          actions={actions}
          description={description}
          icon={icon}
          meta={meta}
          status={status}
          title={title}
          type={type}
        />
        {metrics && metrics.length > 0 ? <MetricStrip metrics={metrics} /> : null}
        {children}
      </section>
    );
  },
);
DetailSurface.displayName = "DetailSurface";

export const DetailSection = React.forwardRef<HTMLElement, DetailSectionProps>(
  function DetailSection(
    { children, className, density = "md", rows, title, ...props },
    ref,
  ) {
    const styles = detailSurfaceVariants();

    return (
      <Card
        ref={ref}
        className={styles.section({ className })}
        density={density}
        {...props}
      >
        <CardHeader density={density}>
          <CardTitle density={density}>{title}</CardTitle>
        </CardHeader>
        <CardContent density={density}>
          {rows ? <MetaGrid rows={rows} /> : children}
        </CardContent>
      </Card>
    );
  },
);
DetailSection.displayName = "DetailSection";
