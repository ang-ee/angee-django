import * as React from "react";

import { useBreadcrumbLeafLabel } from "../chrome/Breadcrumb";
import { tv } from "../lib/variants";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  type CardDensity,
} from "../ui/card";
import { EmptyState, type EmptyStateProps } from "./EmptyState";
import { MetaGrid, type MetaGridProps } from "./MetaGrid";
import { MetricStrip, type MetricTileValue } from "./MetricStrip";
import { RecordHeader, type RecordHeaderProps } from "./RecordHeader";
import { CommandBand, type CommandBandProps } from "../views/resource/CommandBand";
import { Skeleton, SkeletonText } from "../ui/skeleton";

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
    "actions" | "crumbs" | "density" | "description" | "icon" | "lead" | "extra" | "meta" | "status" | "title" | "type"
  > & {
    /** The record's command band (menu · verbs · chrome · pager) above the surface. */
    band?: CommandBandProps;
    children?: React.ReactNode;
    className?: string;
    empty?: DetailSurfaceEmptyState | null | false;
    loading?: boolean;
    loadingMessage?: string;
    metrics?: readonly MetricTileValue[];
    /** Publish this routed record's title into the current breadcrumb. */
    publishBreadcrumbLabel?: boolean;
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

/**
 * @kind token
 */
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
 * @kind fragment
 */
export const DetailSurface = React.forwardRef<HTMLElement, DetailSurfaceProps>(
  function DetailSurface(
    {
      actions,
      band,
      children,
      className,
      crumbs,
      density,
      description,
      empty,
      extra,
      icon,
      lead,
      loading = false,
      loadingMessage,
      meta,
      metrics,
      publishBreadcrumbLabel = false,
      status,
      title,
      type,
      ...props
    },
    ref,
  ) {
    const styles = detailSurfaceVariants();
    useBreadcrumbLeafLabel(
      typeof title === "string" ? title : null,
      publishBreadcrumbLabel,
    );

    if (loading) {
      return <DetailSurfaceSkeleton className={className} density={density} message={loadingMessage} />;
    }
    if (empty) {
      return <EmptyState fill {...empty} />;
    }

    return (
      <>
        {band ? <CommandBand {...band} /> : null}
        <section ref={ref} className={styles.root({ className })} {...props}>
          <RecordHeader
            actions={actions}
            crumbs={crumbs}
            density={density}
            description={description}
            extra={extra}
            icon={icon}
            lead={lead}
            meta={meta}
            status={status}
            title={title}
            type={type}
          />
          {metrics && metrics.length > 0 ? <MetricStrip metrics={metrics} /> : null}
          {children}
        </section>
      </>
    );
  },
);
DetailSurface.displayName = "DetailSurface";

function DetailSurfaceSkeleton({
  className,
  density,
  message,
}: {
  className?: string;
  density?: RecordHeaderProps["density"];
  message?: string;
}): React.ReactElement {
  const styles = detailSurfaceVariants();
  // The record header owns the pending title and the surface's one status; the rest is hidden geometry.
  return (
    <section className={styles.root({ className })}>
      <RecordHeader
        density={density}
        loading
        loadingMessage={message}
        title={null}
        extra={<SkeletonText lines={2} className="max-w-2xl" />}
        actions={<Skeleton className="h-btn-sm w-20" />}
      />
      <MetricStrip loading />
      {Array.from({ length: 2 }, (_, sectionIndex) => (
        <Card key={sectionIndex} aria-hidden="true" className="shadow-none">
          <CardHeader>
            <CardTitle><Skeleton shape="text" size="md" className="w-32" /></CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            {Array.from({ length: 4 }, (_, rowIndex) => (
              <div key={rowIndex} className="space-y-2">
                <Skeleton shape="text" size="sm" className="w-20" />
                <Skeleton shape="text" size="md" className={rowIndex % 2 === 0 ? "w-3/4" : "w-1/2"} />
              </div>
            ))}
          </CardContent>
        </Card>
      ))}
    </section>
  );
}

/**
 * @kind fragment
 */
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
