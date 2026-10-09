import * as React from "react";
import { senderDisplayName, type SenderIdentity } from "@angee/parties";
import { Avatar, Glyph, RelativeTime, avatarInitials } from "@angee/ui";

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
  "sender.party.id",
  "sender.party.display_name",
  "channel.id",
  "channel.display_name",
] as const;

/** One message as an email-style row: avatar, sender, star, subject, a two-line preview and the date. */
export function MessageSummary({ message }: { message: MessageSummaryData }): React.ReactElement {
  const t = useMessagingT();
  const sender = senderDisplayName(message.sender, t("message.unknownSender"));
  return (
    <div className="flex min-w-0 items-start gap-3 whitespace-normal py-3">
      <Avatar size="sm" initials={avatarInitials(sender)} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{sender}</span>
          {message.starred ? (
            <Glyph name="star" className="text-warning-text" label={t("message.starred")} />
          ) : null}
          <span className="ml-auto whitespace-nowrap text-2xs text-fg-muted">
            <RelativeTime value={message.sent_at ?? message.created_at} />
          </span>
        </div>
        {message.title ? <p className="truncate text-13 font-medium">{message.title}</p> : null}
        <p className="line-clamp-2 text-13 text-fg-muted">{message.preview}</p>
        {message.channel ? (
          <p className="truncate text-2xs text-fg-muted">{message.channel.display_name}</p>
        ) : null}
      </div>
    </div>
  );
}
