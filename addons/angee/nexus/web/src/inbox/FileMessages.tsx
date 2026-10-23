import { useMemo } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { MessagePaneRow } from "@angee/messaging";
import { ListView, ResourceViewProvider, useRouteHref, type ChatterViewContext } from "@angee/ui";
import { InboxConnections } from "./documents";
import { relatedSource } from "./sources";
import { COLLECTION_INITIAL, INBOX_MODELS } from "./state";
import { useNexusT } from "../i18n";

function fileTarget(context: ChatterViewContext): string {
  return context.view.kind === "record" && context.view.sqid ? `file:${context.view.sqid}` : "";
}

/** The same authorized, distinct message backlinks as the explorer's Links pane. */
export function useFileMessageCount(context: ChatterViewContext): number | undefined {
  const target = fileTarget(context);
  const query = useAuthoredQuery(InboxConnections, { target, text: "", oldest: false, page: 1, size: 1 }, {
    models: INBOX_MODELS, enabled: Boolean(target), errorNotification: false,
  });
  return query.data?.inbox_connections.message_count;
}

/** Files link into the explorer at the exact message part, with the file pinned. */
export function FileMessages({ context }: { context: ChatterViewContext }) {
  const t = useNexusT();
  const href = useRouteHref();
  const target = fileTarget(context);
  const source = useMemo(() => relatedSource(target, "messages", t), [target, t]);
  const columns = useMemo(() => [{ field: "latest", header: t("inbox.latest") }], [t]);
  return <ResourceViewProvider key={target} scope="local" initialState={COLLECTION_INITIAL}>
    <ListView presentation="embedded" resource="nexus.InboxConnections" source={source}
      selectable={false} columns={columns} availableViews={["list"]} textFilterField="text"
      chrome={{ heading: false }} groupOptions={[]} filterOptions={[]} customFilterFields={[]}
      emptyContent={t("inbox.noRelated")}
      renderPaneRow={(row) => row.message ? <MessagePaneRow message={row.message}
        href={href("nexus.inbox", {}, {
          message: row.message.id, part: row.part?.id, related: target, relatedFrom: row.message.id,
        })} /> : null} />
  </ResourceViewProvider>;
}
