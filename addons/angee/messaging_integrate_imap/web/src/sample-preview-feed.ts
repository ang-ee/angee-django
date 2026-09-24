import { useAuth } from "@angee/app";
import type { DocumentType } from "@angee/gql/console";
import { useAuthoredKeysetFeed, type DocumentVariables } from "@angee/refine";

import { PreviewImapSample } from "./documents";

type Preview = DocumentType<typeof PreviewImapSample>["preview_imap_sample"];
export type SampleRow = Preview["messages"][number] & { id: string };
type PreviewVariables = DocumentVariables<typeof PreviewImapSample>;

/** The adapter's private cursor carries the server's flat continuation contract. */
export function samplePreviewCursor(cursor: string) {
  const [uidvalidity, upperUid, beforeUid, totalCount] = cursor.split(":").map(Number);
  if (uidvalidity === undefined || upperUid === undefined || beforeUid === undefined || totalCount === undefined
    || ![uidvalidity, upperUid, beforeUid, totalCount].every(Number.isSafeInteger)) {
    throw new Error("Invalid IMAP preview continuation.");
  }
  return { uidvalidity, upperUid, beforeUid, totalCount };
}

/** Live mailbox reads run only after an operator explicitly previews or pages. */
export function useSamplePreviewFeed(variables: PreviewVariables) {
  return useAuthoredKeysetFeed({
    actor: useAuth().user?.id,
    enabled: false,
    models: [],
    pageSize: variables.limit,
    queryOptions: {
      staleTime: "static", gcTime: 0, retry: false,
      refetchOnMount: false, refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
    window: {
      document: PreviewImapSample,
      variables: (cursor, _through, limit) => ({
        ...variables,
        ...(cursor === null ? {} : samplePreviewCursor(cursor)),
        limit,
      }),
      select: ({ preview_imap_sample: page }) => ({
        rows: page.messages.map((message) => ({ ...message, id: String(message.uid) })),
        count: page.total_count,
        // Preserve snapshot identity on empty/exhausted pages too. Only
        // has_older controls whether Query uses this as a continuation.
        older_cursor: `${page.uidvalidity}:${page.upper_uid}:${page.next_before_uid ?? 0}:${page.total_count}`,
        has_older: page.next_before_uid != null,
        has_more_in_window: false,
        has_older_than_through: false,
      }),
    },
  });
}
