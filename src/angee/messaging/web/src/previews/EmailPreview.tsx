import PostalMime, { type Address, type Attachment } from "postal-mime";
import * as React from "react";
import {
  EmptyState,
  Glyph,
  MessageAttachmentChip,
  MessageHtmlFrame,
  PreviewFileContent,
  SegmentedControl,
  formatSize,
  textRoleVariants,
  type PreviewFileReadContext,
  type PreviewProviderProps,
} from "@angee/ui";

import { useMessagingT } from "../i18n";
import { MessageEnvelope, type RecipientRole } from "../MessageEnvelope";
import { EMAIL_PREVIEW_MAX_BYTES } from "./email-size";

type EmailFormat = "html" | "text";

/** Only what the preview shows; the parsed message and its attachment bytes are not kept. */
interface EmailView {
  subject?: string;
  sender?: { name: string; address?: string };
  recipients: Partial<Record<RecipientRole, string[]>>;
  date?: string;
  html?: string;
  text?: string;
  /** The first `Content-Language` tag, when the message declares one. */
  lang?: string;
  /** Content id → data URL, for the images the HTML references. */
  inlineUrls: Record<string, string>;
  attachments: { name: string | null; size: number }[];
}

function addressLabel(address: Address): string {
  if (address.group) return [address.name, ...address.group.map(addressLabel)].filter(Boolean).join(", ");
  return address.name && address.address ? `${address.name} <${address.address}>` : address.address || address.name;
}

function attachmentBytes(content: Attachment["content"]): Uint8Array {
  if (typeof content === "string") return new TextEncoder().encode(content);
  return content instanceof Uint8Array ? content : new Uint8Array(content);
}

function base64(bytes: Uint8Array): string {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

/** Parse the fetched RFC 822 bytes once; postal-mime owns MIME, transfer encodings and charsets. */
async function readEmail(response: Response, { signal }: PreviewFileReadContext, tooLarge: string): Promise<EmailView> {
  const raw = await response.arrayBuffer();
  // A file of unknown size is checked here; one of known size never reaches the renderer.
  if (raw.byteLength > EMAIL_PREVIEW_MAX_BYTES) throw new Error(tooLarge);
  const email = await PostalMime.parse(raw);
  signal.throwIfAborted();
  const inlineUrls: Record<string, string> = {};
  const attachments: EmailView["attachments"] = [];
  for (const attachment of email.attachments) {
    const cid = attachment.contentId?.replace(/^<|>$/g, "").trim();
    const bytes = attachmentBytes(attachment.content);
    if (cid && attachment.mimeType.startsWith("image/") && email.html?.includes(`cid:${cid}`)) {
      if (!Object.hasOwn(inlineUrls, cid)) inlineUrls[cid] = `data:${attachment.mimeType};base64,${base64(bytes)}`;
      continue;
    }
    attachments.push({ name: attachment.filename, size: bytes.byteLength });
  }
  const sender = email.from && !email.from.group
    ? { name: email.from.name || email.from.address, address: email.from.address }
    : email.from ? { name: addressLabel(email.from) } : undefined;
  const lang = email.headers.find((header) => header.key === "content-language")?.value.split(",")[0]?.trim();
  return {
    ...(email.subject ? { subject: email.subject } : {}),
    ...(sender ? { sender } : {}),
    recipients: {
      TO: (email.to ?? []).map(addressLabel),
      CC: (email.cc ?? []).map(addressLabel),
      BCC: (email.bcc ?? []).map(addressLabel),
    },
    ...(email.date ? { date: email.date } : {}),
    ...(email.html ? { html: email.html } : {}),
    ...(email.text ? { text: email.text } : {}),
    ...(lang ? { lang } : {}),
    inlineUrls,
    attachments,
  };
}

/**
 * The preview of an email file (`message/rfc822`): the subject over the message's
 * envelope, the body with a toggle between the HTML and plain-text alternatives when
 * both exist, and the attachments under it. The HTML renders in the shared message
 * sandbox, with remote images blocked until the reader loads them.
 */
export default function EmailPreview({ file }: PreviewProviderProps): React.ReactElement {
  const t = useMessagingT();
  return (
    <PreviewFileContent url={file.url} read={(response, context) => readEmail(response, context, t("email.tooLarge"))}>
      {(view) => <EmailMessage {...view} />}
    </PreviewFileContent>
  );
}

function EmailMessage({ subject, sender, recipients, date, html, text, lang, inlineUrls, attachments }: EmailView): React.ReactElement {
  const t = useMessagingT();
  const [format, setFormat] = React.useState<EmailFormat>(html ? "html" : "text");
  const options = [
    { value: "html" as const, label: t("email.html") },
    { value: "text" as const, label: t("email.text") },
  ];
  return (
    <div className="flex h-full min-h-0 flex-col bg-sheet">
      <div className="grid gap-3 border-b border-border-subtle px-4 py-3">
        <p lang={lang} className={textRoleVariants({ role: "title" })}>{subject || t("email.noSubject")}</p>
        <MessageEnvelope
          sender={sender?.name || t("message.unknownSender")}
          address={sender?.address}
          recipients={recipients}
          date={date}
        />
        {html && text ? (
          <SegmentedControl className="justify-self-start" aria-label={t("email.format")} options={options} value={format} onValueChange={setFormat} />
        ) : null}
      </div>
      {format === "html" && html ? (
        <MessageHtmlFrame html={html} title={t("email.htmlBody")} lang={lang} inlineUrls={inlineUrls}
          className="h-auto min-h-96 flex-1 rounded-none border-0" />
      ) : text ? (
        <div lang={lang} className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 text-sm text-fg">{text}</div>
      ) : (
        <EmptyState title={t("email.noBody")} />
      )}
      {attachments.length ? (
        <ul aria-label={t("email.attachments")} className="flex flex-wrap gap-2 border-t border-border-subtle px-4 py-3">
          {attachments.map((attachment, index) => (
            <li key={index} className="min-w-0">
              <MessageAttachmentChip
                icon={<Glyph decorative name="attachment" />}
                remove={<span className="shrink-0 text-2xs opacity-70">{formatSize(attachment.size)}</span>}
              >
                {attachment.name || t("email.attachment")}
              </MessageAttachmentChip>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
