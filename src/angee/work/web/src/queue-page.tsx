import { CommandBand, Page, PageBody, RecordHeader, SkeletonStatus, SkeletonText, type PageBodyProps } from "@angee/ui";
import * as React from "react";

/**
 * A queue's operational page (triage, board, cycles) as a record page: the shell's command
 * band carries the page's verbs, the record header names the queue (eyebrow · title · what the
 * page does), and the body is the collection. The pages contribute content; the shape is the
 * framework's. Until its record names it, the title is a skeleton announcing `loadingLabel`.
 */
export function QueueRecordPage({ eyebrow, title, loadingLabel, description, verbs, gutter, scroll, children }: {
  eyebrow: React.ReactNode;
  title: string | null | undefined;
  loadingLabel: string;
  description?: React.ReactNode;
  verbs?: React.ReactNode;
  gutter?: PageBodyProps["gutter"];
  scroll?: PageBodyProps["scroll"];
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <Page>
      <CommandBand verbs={verbs} />
      <RecordHeader density="compact" headingLevel={1} crumbs={eyebrow} description={description} title={title
        ?? <SkeletonStatus label={loadingLabel} className="py-0.5"><SkeletonText lines={1} size="md" lineClassName="w-48" /></SkeletonStatus>} />
      <PageBody gutter={gutter} scroll={scroll}>{children}</PageBody>
    </Page>
  );
}
