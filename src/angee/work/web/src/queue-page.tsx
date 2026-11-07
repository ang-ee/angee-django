import { CommandBand, Page, PageBody, RecordHeader, type PageBodyProps, type RecordHeaderProps } from "@angee/ui";
import * as React from "react";

/**
 * A queue's operational page (triage, board, cycles) as a record page: the shell's command
 * band carries the page's verbs, the record header names the queue (eyebrow · title · what the
 * page does), and the body is the collection. The pages contribute content; the shape is the
 * framework's, including the header's pending and unavailable title.
 */
export function QueueRecordPage({ eyebrow, description, verbs, gutter, scroll, children, ...header }: Pick<
  RecordHeaderProps, "title" | "loading" | "loadingMessage" | "unavailable"
> & {
  eyebrow: React.ReactNode;
  description?: React.ReactNode;
  verbs?: React.ReactNode;
  gutter?: PageBodyProps["gutter"];
  scroll?: PageBodyProps["scroll"];
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <Page>
      <CommandBand verbs={verbs} />
      <RecordHeader density="compact" headingLevel={1} crumbs={eyebrow} description={description} {...header} />
      <PageBody gutter={gutter} scroll={scroll}>{children}</PageBody>
    </Page>
  );
}
