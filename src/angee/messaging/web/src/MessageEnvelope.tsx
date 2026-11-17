import type * as React from "react";
import { Avatar, avatarInitials, formatDateTime } from "@angee/ui";

import { useMessagingT } from "./i18n";

/** The envelope roles listed under the sender, in header order. */
export const RECIPIENT_ROLES = ["TO", "CC", "BCC"] as const;
export type RecipientRole = (typeof RECIPIENT_ROLES)[number];

export interface MessageEnvelopeProps {
  /** The sender's shown name. */
  sender: string;
  /** The sender's address, shown beside the name when it differs. */
  address?: string | null;
  avatarUrl?: string;
  /** The names (or name and address) per recipient role; an empty role is left out. */
  recipients: Partial<Record<RecipientRole, readonly string[]>>;
  /** When the message was sent; absolute, like every facts line. */
  date?: string | null;
  /** More lines under the sender, such as a status update's channel. */
  children?: React.ReactNode;
  /** Controls after the date, such as the star and the host's commands. */
  actions?: React.ReactNode;
}

/**
 * The envelope of a message read like an email: the sender with avatar and address,
 * the To, Cc and Bcc lines, and the date. The one header for the message reader and
 * the email file preview.
 */
export function MessageEnvelope({ sender, address, avatarUrl, recipients, date, children, actions }: MessageEnvelopeProps): React.ReactElement {
  const t = useMessagingT();
  const labels: Record<RecipientRole, string> = { TO: t("message.to"), CC: t("message.cc"), BCC: t("message.bcc") };
  return (
    <header className="flex flex-wrap items-start gap-3">
      <Avatar src={avatarUrl} initials={avatarInitials(sender)} />
      <div className="min-w-40 flex-1 space-y-0.5">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="truncate text-13 font-semibold">{sender}</span>
          {address && address !== sender ? (
            <span className="truncate text-12 text-fg-muted">{address}</span>
          ) : null}
        </div>
        {children}
        {RECIPIENT_ROLES.map((role) => {
          const names = recipients[role]?.filter(Boolean) ?? [];
          return names.length ? (
            <div key={role} className="truncate text-12 text-fg-muted">
              {labels[role]} {names.join(", ")}
            </div>
          ) : null;
        })}
      </div>
      {/* The date and the controls wrap below the sender as one group rather than
          overlap it when the column is narrow. */}
      <div className="ml-auto flex shrink-0 items-start gap-3">
        {date ? (
          <time className="whitespace-nowrap pt-0.5 text-12 text-fg-muted" dateTime={date}>
            {formatDateTime(date) || date}
          </time>
        ) : null}
        {actions}
      </div>
    </header>
  );
}
