import * as React from "react";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { Button, Collapsible, ErrorBanner, LoadingPanel, PaneRow, PaneRows, errorMessage, useResourceRecordHrefLookup } from "@angee/ui";

import { IdentifyMessageSourceCopyDocument, MessageSourceCopiesDocument, type ThreadTranscriptRow } from "./documents";
import { useMessagingT } from "./i18n";

const COPY_MODELS = ["messaging.Message", "messaging.Thread", "provenance.Changeset"] as const;

interface SourceCopiesProps {
  messageId: string;
  renderActions?: (message: ThreadTranscriptRow) => React.ReactNode;
}

/** The message's physical source copies, folded under the row: inspection and reply
 *  commands always receive the chosen physical source row. */
export function MessageSourceCopies({ messageId, channelName, renderActions }: SourceCopiesProps & {
  channelName?: string | null;
}): React.ReactElement {
  const t = useMessagingT();
  const [open, setOpen] = React.useState(false);
  return (
    <Collapsible.Root open={open} onOpenChange={setOpen}>
      <Collapsible.Trigger render={<Button size="sm" variant="ghost" />}>
        <Collapsible.Icon />
        {channelName ? t("copies.transport", { channel: channelName }) : t("copies.inspect")}
      </Collapsible.Trigger>
      <Collapsible.Panel>
        {/* The inspector's query and mutation mount only once opened: a transcript row stays cheap. */}
        {open ? <SourceCopiesPanel messageId={messageId} renderActions={renderActions} /> : null}
      </Collapsible.Panel>
    </Collapsible.Root>
  );
}

function SourceCopiesPanel({ messageId, renderActions }: SourceCopiesProps): React.ReactElement {
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
  if (copies.isFetching && copies.data === undefined) return <LoadingPanel message={t("copies.loading")} density="inline" />;
  return (
    <>
      <ErrorBanner description={error ?? copies.error?.message ?? null} />
      <PaneRows>
        {copies.data?.message_source_copies.map((copy) => {
          const href = recordHref("messaging.Message", copy.id);
          const canSeparate = copy.id !== messageId && current?.permissions.includes("write")
            && copy.permissions.includes("write");
          return (
            <li key={copy.id}>
              <PaneRow
                icon="channel"
                title={copy.channel?.display_name || t("copies.unknownTransport")}
                detail={copy.id === messageId ? t("copies.thisCopy") : undefined}
                href={href}
                label={href ? t("copies.open") : undefined}
                verb={<>
                  {renderActions?.(copy)}
                  {canSeparate ? (
                    <Button size="sm" variant="ghost" disabled={mutation.fetching} onClick={() => void separate(copy.id)}>
                      {t("copies.separate")}
                    </Button>
                  ) : null}
                </>}
              />
            </li>
          );
        })}
      </PaneRows>
    </>
  );
}
