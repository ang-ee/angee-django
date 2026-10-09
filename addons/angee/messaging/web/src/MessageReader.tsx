import * as React from "react";
import { useAuthoredMutation } from "@angee/refine";
import { senderDisplayName } from "@angee/parties";
import {
  Alert,
  Avatar,
  Button,
  Glyph,
  MessagePartsView,
  PreviewPane,
  RelativeTime,
  avatarInitials,
  cn,
  type MessagePart,
} from "@angee/ui";

import { SetMessageStarredDocument, type MessageReaderData } from "./documents";
import { useMessagingT } from "./i18n";

// The envelope roles listed under the sender, in header order.
const RECIPIENT_ROLES = ["TO", "CC", "BCC"] as const;
const STAR_MODELS = ["messaging.MessageStar", "messaging.Message"];

export interface MessageReaderProps {
  message: MessageReaderData;
  /** The attachment shown beside the body. The host owns where this lives: route or local state. */
  previewPartId?: string | null;
  onPreviewPartChange: (partId: string | null) => void;
  /** Reveal and scroll to one part of the body. */
  activePartId?: string | null;
  /** The host's per-part actions, such as backlinks. */
  renderPartActions?: (part: MessagePart) => React.ReactNode;
  /** The host's commands, after the star in the header. */
  actions?: React.ReactNode;
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
  children,
}: MessageReaderProps): React.ReactElement {
  const t = useMessagingT();
  const [setStarred, starState] = useAuthoredMutation(SetMessageStarredDocument, { invalidateModels: STAR_MODELS });
  const [starError, setStarError] = React.useState<string>();
  const recipientLabels = { TO: t("message.to"), CC: t("message.cc"), BCC: t("message.bcc") };
  const sender = senderDisplayName(message.sender, t("message.unknownSender"));
  const address = message.sender?.value;
  const preview = previewPartId ? message.parts.find((part) => part.id === previewPartId) : undefined;
  const previewUrl = preview?.file?.url;
  return (
    <div className={cn("grid min-w-0 gap-6", previewUrl && "lg:grid-cols-2")}>
      <article aria-label={message.title || t("message.label")} className="min-w-0 space-y-5">
        <header className="flex items-start gap-3">
          <Avatar initials={avatarInitials(sender)} />
          <div className="min-w-0 flex-1 space-y-0.5">
            <div className="flex min-w-0 items-baseline gap-2">
              <span className="truncate text-13 font-semibold">{sender}</span>
              {address && address !== sender ? (
                <span className="truncate text-2xs text-fg-muted">{address}</span>
              ) : null}
            </div>
            {RECIPIENT_ROLES.map((role) => {
              const names = message.participants
                .filter((participant) => participant.role === role)
                .map((participant) => senderDisplayName(participant.handle))
                .filter(Boolean);
              return names.length ? (
                <div key={role} className="truncate text-2xs text-fg-muted">
                  {recipientLabels[role]} {names.join(", ")}
                </div>
              ) : null;
            })}
          </div>
          <RelativeTime
            className="whitespace-nowrap pt-0.5 text-2xs text-fg-muted"
            value={message.sent_at ?? message.created_at}
          />
          <Button
            size="iconSm"
            variant={message.starred ? "secondary" : "ghost"}
            aria-label={t(message.starred ? "message.unstar" : "message.star")}
            aria-pressed={message.starred}
            disabled={starState.fetching}
            onClick={() => {
              setStarError(undefined);
              void setStarred({ id: message.id, starred: !message.starred }).catch((error: unknown) =>
                setStarError(error instanceof Error ? error.message : String(error)),
              );
            }}
          >
            <Glyph name="star" />
          </Button>
          {actions}
        </header>
        {starError ? <Alert tone="danger">{starError}</Alert> : null}
        <MessagePartsView
          parts={message.parts}
          activePartId={activePartId ?? previewPartId}
          onPreviewFile={(part) => onPreviewPartChange(part.id ?? null)}
          renderPartActions={renderPartActions}
        />
        {message.parts.length === 0 ? <p className="whitespace-pre-wrap text-13">{message.preview}</p> : null}
        {children}
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
