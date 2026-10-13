import * as React from "react";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { Alert, Button, NavLink, errorMessage, useResourceRecordHrefLookup } from "@angee/ui";

import { IdentifyMessageSourceCopyDocument, MessageSourceCopiesDocument, type ThreadTranscriptRow } from "./documents";
import { useMessagingT } from "./i18n";

const COPY_MODELS = ["messaging.Message", "messaging.Thread", "provenance.Changeset"] as const;

interface SourceCopiesProps {
  messageId: string;
  renderActions?: (message: ThreadTranscriptRow) => React.ReactNode;
}

/** Inspection and reply commands always receive the chosen physical source row. */
export function MessageSourceCopies({ messageId, channelName, renderActions }: SourceCopiesProps & {
  channelName?: string | null;
}): React.ReactElement {
  const t = useMessagingT();
  const panelId = React.useId();
  const [open, setOpen] = React.useState(false);
  return <div className="space-y-2 text-2xs text-fg-muted">
    {channelName ? <p>{t("copies.transport", { channel: channelName })}</p> : null}
    <Button size="sm" variant="ghost" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}>
      {t("copies.inspect")}
    </Button>
    {/* The inspector's query and mutation mount only once opened: a transcript row stays cheap. */}
    {open ? <SourceCopiesPanel id={panelId} messageId={messageId} renderActions={renderActions} /> : null}
  </div>;
}

function SourceCopiesPanel({ id, messageId, renderActions }: SourceCopiesProps & { id: string }): React.ReactElement {
  const t = useMessagingT();
  const recordHref = useResourceRecordHrefLookup();
  const copies = useAuthoredQuery(MessageSourceCopiesDocument, { id: messageId }, { models: COPY_MODELS });
  const [identify, mutation] = useAuthoredMutation(IdentifyMessageSourceCopyDocument, { invalidateModels: COPY_MODELS });
  const [error, setError] = React.useState<string>();
  const current = copies.data?.message_source_copies.find((copy) => copy.id === messageId);
  const separate = async (copyId: string) => {
    setError(undefined);
    try {
      // The authored mutation rejects on envelope and transport errors alike.
      await identify({ left: messageId, right: copyId, same: false });
    } catch (cause) {
      setError(errorMessage(cause, t("copies.failed")));
    }
  };
  return <div id={id} className="space-y-2 rounded border border-border-subtle p-3">
    {copies.isFetching && copies.data === undefined ? <p>{t("copies.loading")}</p> : null}
    {copies.error || error ? <Alert tone="danger">{error || copies.error?.message}</Alert> : null}
    {copies.data?.message_source_copies.map((copy) => {
      const href = recordHref("messaging.Message", copy.id);
      const canSeparate = copy.id !== messageId && current?.permissions.includes("write")
        && copy.permissions.includes("write");
      return <div key={copy.id} className="space-y-1 border-b border-border-subtle pb-2 last:border-0">
        <p>{t("copies.transport", { channel: copy.channel?.display_name || t("copies.unknownTransport") })}</p>
        <p className="break-all">{copy.external_id}</p>
        <div className="flex flex-wrap items-center gap-2">
          {href ? <NavLink href={href} variant="inline">{t("copies.open")}</NavLink> : null}
          {renderActions?.(copy)}
          {canSeparate ? <Button size="sm" variant="ghost" disabled={mutation.fetching} onClick={() => void separate(copy.id)}>
            {t("copies.separate")}
          </Button> : null}
        </div>
      </div>;
    })}
  </div>;
}
