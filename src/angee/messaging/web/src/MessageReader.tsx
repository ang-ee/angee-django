import * as React from "react";
import { useAuthoredMutation } from "@angee/refine";
import { senderAvatarUrl, senderDisplayName } from "@angee/parties";
import {
  Button,
  ErrorBanner,
  Glyph,
  MessagePartsView,
  PreviewPane,
  cn,
  useResourceRecordHrefLookup,
  type MessagePart,
} from "@angee/ui";

import { SetMessageStarredDocument, type MessageReaderData, type MessageReplyData } from "./documents";
import { useMessagingT } from "./i18n";
import { isStatusUpdate, useMessageChannelLabel } from "./MessageSummary";
import { MessageEnvelope, RECIPIENT_ROLES } from "./MessageEnvelope";
import { MessageSourceCopies } from "./MessageSourceCopies";
import { StarButton } from "./StarButton";
import { MessageReplyQuote } from "./MessageReplyQuote";

const STAR_MODELS = ["messaging.MessageStar", "messaging.Message"];

export interface MessageReaderProps {
  message: MessageReaderData;
  /** The attachment shown beside the body. The host owns where this lives: route or local state. */
  previewPartId?: string | null;
  /** An attachment chip asks the host to preview a part; Close passes null. A host that
   *  previews elsewhere handles the part there and passes no `previewPartId`. */
  onPreviewPartChange: (partId: string | null) => void;
  /** Reveal and scroll to one part of the body. */
  activePartId?: string | null;
  /** The host's per-part actions, such as backlinks. */
  renderPartActions?: (part: MessagePart) => React.ReactNode;
  /** The host's commands, after the star in the header. */
  actions?: React.ReactNode;
  /** Open the quoted parent in the host's conversation context. */
  onOpenReply?: (message: MessageReplyData) => void;
  /** The host's content after the body. */
  children?: React.ReactNode;
}

/**
 * One message read like an email: the sender, its recipients, the date and the
 * reader's star above the body; an attachment opens beside the body.
 */
export function MessageReader({
  message,
  previewPartId,
  onPreviewPartChange,
  activePartId,
  renderPartActions,
  actions,
  onOpenReply,
  children,
}: MessageReaderProps): React.ReactElement {
  const t = useMessagingT();
  const channelLabel = useMessageChannelLabel();
  const recordHref = useResourceRecordHrefLookup();
  const [setStarred, starState] = useAuthoredMutation(SetMessageStarredDocument, { invalidateModels: STAR_MODELS });
  const [starError, setStarError] = React.useState<string>();
  const sender = senderDisplayName(message.sender, t("message.unknownSender"));
  const recipients = Object.fromEntries(RECIPIENT_ROLES.map((role) => [role, message.recipients
    .filter((recipient) => recipient.role === role)
    .map((recipient) => senderDisplayName(recipient.handle))]));
  const preview = previewPartId ? message.parts.find((part) => part.id === previewPartId) : undefined;
  const previewUrl = preview?.file?.url;
  return (
    <div className={cn("grid min-w-0 gap-6", previewUrl && "lg:grid-cols-2")}>
      <article aria-label={message.title || t("message.label")} className="min-w-0 space-y-5">
        <MessageEnvelope
          sender={sender}
          address={message.sender?.value}
          avatarUrl={senderAvatarUrl(message.sender)}
          recipients={recipients}
          date={message.sent_at ?? message.created_at}
          actions={<>
            <StarButton
              starred={Boolean(message.starred)}
              disabled={starState.fetching}
              onToggle={() => {
                setStarError(undefined);
                void setStarred({ id: message.id, starred: !message.starred }).catch((error: unknown) =>
                  setStarError(error instanceof Error ? error.message : String(error)),
                );
              }}
            />
            {actions}
          </>}
        >
          {isStatusUpdate(message) ? <p className="text-12 text-fg-muted">{channelLabel(message)}</p> : null}
        </MessageEnvelope>
        <ErrorBanner description={starError ?? null} />
        {message.parent ? <MessageReplyQuote message={message.parent}
          href={onOpenReply ? undefined : recordHref("messaging.Message", message.parent.id)}
          onOpen={onOpenReply ? () => onOpenReply(message.parent!) : undefined} /> : null}
        <MessagePartsView
          parts={message.parts}
          activePartId={activePartId ?? previewPartId}
          onPreviewFile={(part) => onPreviewPartChange(part.id ?? null)}
          renderPartActions={renderPartActions}
        />
        {message.parts.length === 0 ? <p className="whitespace-pre-wrap text-13">{message.preview}</p> : null}
        {children}
        <MessageSourceCopies messageId={message.id} channelName={message.channel?.display_name} />
      </article>
      {preview && previewUrl ? (
        <aside aria-label={t("message.attachmentPreview")} className="min-w-0 space-y-3">
          <div className="flex items-center gap-2">
            <Glyph name="attachment" decorative />
            <span className="min-w-0 flex-1 truncate text-13 font-medium">
              {preview.name || preview.file?.title || preview.file?.filename || t("message.attachment")}
            </span>
            <Button size="sm" variant="ghost" onClick={() => onPreviewPartChange(null)}>
              <Glyph name="x" />
              {t("message.closePreview")}
            </Button>
          </div>
          <PreviewPane
            file={{
              identity: preview.file ? { id: preview.file.id, version: preview.file.content_hash } : undefined,
              url: previewUrl,
              name: preview.name || preview.file?.filename || t("message.attachment"),
              mime: preview.type || preview.file?.mime_type?.mime_type,
              size: preview.file?.size_bytes,
            }}
          />
        </aside>
      ) : null}
    </div>
  );
}
