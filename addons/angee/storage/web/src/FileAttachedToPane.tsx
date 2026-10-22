import { useAuthoredQuery } from "@angee/refine";
import {
  ErrorBanner, PaneRows, RecordReferenceRow, Skeleton, SkeletonStatus,
  type ChatterViewContext, type ContainerChild, type RelatedGroupContent,
} from "@angee/ui";
import type { ReactElement } from "react";

import { FILE_ATTACHMENT_MODEL, StorageFileAttachments } from "./data/documents";
import { useStorageT } from "./i18n";

function useFileAttachments(context: ChatterViewContext) {
  const file = context.view.kind === "record" ? context.view.sqid ?? "" : "";
  const query = useAuthoredQuery(StorageFileAttachments, { file }, {
    enabled: Boolean(file), models: [FILE_ATTACHMENT_MODEL],
  });
  // The reference owner redacts unreadable or deleted target identities.
  const attachments = query.data?.file_attachments.flatMap((edge) => edge.model_label && edge.record_id
    ? [{ key: edge.id, model: edge.model_label, id: edge.record_id }] : []);
  return { query, attachments };
}

/** The records the open file is attached to, on the file's Links tab. */
export const fileAttachedToGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 10,
  content: {
    label: "Attached to",
    useCount: (context) => useFileAttachments(context).attachments?.length,
    render: (context, label) => <>{label()}<li><FileAttachedToPane context={context} /></li></>,
  },
};

function FileAttachedToPane({ context }: { context: ChatterViewContext }): ReactElement {
  const t = useStorageT();
  const { query, attachments } = useFileAttachments(context);
  if (!attachments) {
    if (query.error) return <ErrorBanner description={query.error.message} />;
    return <SkeletonStatus label={t("attachedTo.loading")}><Skeleton className="h-8" /></SkeletonStatus>;
  }
  return <PaneRows>{attachments.map((edge) => <li key={edge.key}>
    <RecordReferenceRow model={edge.model} id={edge.id} references={attachments} />
  </li>)}</PaneRows>;
}
