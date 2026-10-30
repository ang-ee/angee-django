import * as React from "react";
import { senderAvatarUrl, senderDisplayName, type SenderIdentity } from "@angee/parties";
import { AbsoluteTime, Avatar, Button, Glyph, PaneRow, avatarInitials, optionLabel, useEnumOptions, type PaneRowProps } from "@angee/ui";

import { useMessagingT } from "./i18n";

/** The facts a message's summary row shows, as the `messages` resource returns them. */
export interface MessageSummaryData {
  title?: string | null;
  preview: string;
  starred?: boolean | null;
  sent_at?: string | null;
  created_at: string;
  sender?: SenderIdentity | null;
  channel?: { display_name: string } | null;
  platform?: string | null;
  thread?: { modality?: string | null } | null;
}

const MESSAGE_MODEL = "messaging.Message";

/** A source-owned one-way status, never a conversation with the other authors. */
export function isStatusUpdate(message: Pick<MessageSummaryData, "thread">): boolean {
  return message.thread?.modality === "STATUS";
}

/** Where a message came from: its channel's name, else its platform's authored label. */
export function useMessageChannelLabel(): (message: Pick<MessageSummaryData, "channel" | "platform" | "thread">) => string | undefined {
  const t = useMessagingT();
  const platforms = useEnumOptions(MESSAGE_MODEL, "platform", { casing: "upper" });
  return React.useCallback((message) => {
    const option = message.platform ? optionLabel(platforms, message.platform) : undefined;
    const label = typeof option === "string" ? option : undefined;
    if (isStatusUpdate(message)) return message.channel?.display_name
      ? t("message.updateSource", { platform: label ?? "", account: message.channel.display_name })
      : t("message.statusUpdate");
    if (message.channel?.display_name) return message.channel.display_name;
    return typeof label === "string" ? label : undefined;
  }, [platforms, t]);
}

/** Messaging's compact pointer for a pane; the caller owns its destination. */
export function MessagePaneRow({ message, onOpenConversation, ...props }: {
  message: MessageSummaryData & { platform?: string | null };
  onOpenConversation?: () => void;
} & Pick<PaneRowProps, "onOpen" | "href" | "selected" | "detail" | "sub" | "meta" | "icon" | "verb">): React.ReactElement {
  const t = useMessagingT();
  const channelLabel = useMessageChannelLabel();
  const update = isStatusUpdate(message);
  const source = channelLabel(message);
  const sender = senderDisplayName(message.sender, t("message.unknownSender"));
  return <PaneRow icon="channel" title={update ? sender : message.title || sender}
    avatar={props.icon ? undefined : { src: senderAvatarUrl(message.sender), initials: avatarInitials(sender) }}
    detail={source}
    meta={<AbsoluteTime value={message.sent_at || message.created_at} display="date" />}
    sub={[message.title && !update ? sender : undefined, message.preview].filter(Boolean).join(" · ")}
    verb={onOpenConversation && !update ? <Button size="iconSm" variant="ghost" onClick={onOpenConversation}
      aria-label={t("conversations.open")} title={t("conversations.open")}><Glyph name="channel" /></Button> : t("message.read")}
    {...props}
    {...(update ? { detail: props.detail ? <>{source} · {props.detail}</> : source } : {})} />;
}

/** The leaf paths a resource list selects so its rows can render as `MessageSummary`. */
export const MESSAGE_SUMMARY_FIELDS = [
  "title",
  "preview",
  "starred",
  "sent_at",
  "created_at",
  "sender.id",
  "sender.display_name",
  "sender.value",
  "sender.party_link_confirmed",
  "sender.avatar.url",
  "sender.party.id",
  "sender.party.display_name",
  "sender.party.display_avatar.url",
  "channel.id",
  "channel.display_name",
  "platform",
  "thread.modality",
] as const;

/** One message as an email-style row: avatar, sender, star, subject, a two-line preview and the date. */
export function MessageSummary({ message }: { message: MessageSummaryData }): React.ReactElement {
  const t = useMessagingT();
  const channelLabel = useMessageChannelLabel();
  const sender = senderDisplayName(message.sender, t("message.unknownSender"));
  return (
    <div className="flex min-w-0 items-start gap-3 whitespace-normal py-3">
      <Avatar size="sm" src={senderAvatarUrl(message.sender)} initials={avatarInitials(sender)} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{sender}</span>
          {message.starred ? (
            <Glyph name="star" className="text-warning-text" label={t("message.starred")} />
          ) : null}
          <span className="ml-auto whitespace-nowrap text-12 text-fg-muted">
            <AbsoluteTime value={message.sent_at ?? message.created_at} display="date" />
          </span>
        </div>
        {message.title && !isStatusUpdate(message) ? <p className="truncate text-13 font-medium">{message.title}</p> : null}
        <p className="line-clamp-2 text-13 text-fg-muted">{message.preview}</p>
        {channelLabel(message) ? (
          <p className="truncate text-12 text-fg-muted">{channelLabel(message)}</p>
        ) : null}
      </div>
    </div>
  );
}
