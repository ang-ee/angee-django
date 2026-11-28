import type { ReactElement, ReactNode } from "react";
import { RecordPager, RegisteredFormView, type RecordNavigation } from "@angee/ui";

/** Routed Files compose the same complete form as record peeks and relation dialogs. */
export function FilePreviewFrame({
  id, crumbs, closeDetail, fileNavigation,
}: {
  id: string;
  crumbs?: ReactNode;
  closeDetail: () => void;
  fileNavigation: RecordNavigation | null;
}): ReactElement {
  return <RegisteredFormView resource="storage.File" id={id} eyebrow={crumbs}
    onTrashed={closeDetail}
    toolbar={fileNavigation ? <RecordPager navigation={fileNavigation} /> : undefined} />;
}
