import PostalMime from "postal-mime";
import * as React from "react";
import {
  AbsoluteTime,
  EmptyState,
  MessageHtmlFrame,
  PreviewFileContent,
  SegmentedControl,
  textRoleVariants,
  type PreviewProviderProps,
} from "@angee/ui";

import { useMessagingT } from "../i18n";

type ParsedEmail = Awaited<ReturnType<typeof PostalMime.parse>>;
type EmailAddress = NonNullable<ParsedEmail["from"]>;
type EmailFormat = "html" | "text";

/** A parsed email with the content ids its HTML may reference resolved to data URLs. */
interface EmailPreviewContent {
  email: ParsedEmail;
  inlineUrls: Readonly<Record<string, string>>;
  /** The first `Content-Language` tag, when the message declares one. */
  lang?: string;
}

/** Parse the fetched RFC 822 bytes once; postal-mime owns MIME, transfer encodings and charsets. */
async function readEmail(response: Response): Promise<EmailPreviewContent> {
  const email = await PostalMime.parse(await response.arrayBuffer(), { attachmentEncoding: "base64" });
  const inlineUrls: Record<string, string> = {};
  for (const attachment of email.attachments) {
    const cid = attachment.contentId?.replace(/^<|>$/g, "").trim();
    if (!cid || typeof attachment.content !== "string" || !attachment.mimeType.startsWith("image/")) continue;
    if (!Object.hasOwn(inlineUrls, cid)) inlineUrls[cid] = `data:${attachment.mimeType};base64,${attachment.content}`;
  }
  const lang = email.headers.find((header) => header.key === "content-language")?.value.split(",")[0]?.trim();
  return { email, inlineUrls, ...(lang ? { lang } : {}) };
}

function addressLabel(address: EmailAddress): string {
  if ("group" in address && address.group) return [address.name, ...address.group.map(addressLabel)].filter(Boolean).join(", ");
  const mailbox = "address" in address ? address.address : undefined;
  return address.name && mailbox ? `${address.name} <${mailbox}>` : mailbox || address.name;
}

/**
 * The preview of an email file (`message/rfc822`): subject, sender, recipients and
 * date over the body, with a toggle between the HTML and plain-text alternatives
 * when the message carries both. The HTML renders in the shared message sandbox.
 */
export default function EmailPreview({ file }: PreviewProviderProps): React.ReactElement {
  return (
    <PreviewFileContent url={file.url} read={readEmail}>
      {(content) => <EmailMessage {...content} />}
    </PreviewFileContent>
  );
}

function EmailMessage({ email, inlineUrls, lang }: EmailPreviewContent): React.ReactElement {
  const t = useMessagingT();
  const [format, setFormat] = React.useState<EmailFormat>(email.html ? "html" : "text");
  const recipients = (email.to ?? []).map(addressLabel).filter(Boolean).join(", ");
  const options = [
    { value: "html" as const, label: t("email.html") },
    { value: "text" as const, label: t("email.text") },
  ];
  return (
    <div className="flex h-full min-h-0 flex-col bg-sheet">
      <div className="grid gap-1 border-b border-border-subtle px-4 py-3">
        <p className={textRoleVariants({ role: "title" })}>{email.subject || t("email.noSubject")}</p>
        {email.from ? <p className={textRoleVariants({ role: "meta", truncate: true })}>{t("email.from", { address: addressLabel(email.from) })}</p> : null}
        {recipients ? <p className={textRoleVariants({ role: "meta", truncate: true })}>{t("email.to", { addresses: recipients })}</p> : null}
        {email.date ? <AbsoluteTime value={email.date} display="datetime" fallback={email.date} className={textRoleVariants({ role: "meta" })} /> : null}
        {email.html && email.text ? (
          <SegmentedControl className="mt-2 justify-self-start" aria-label={t("email.format")} options={options} value={format} onValueChange={setFormat} />
        ) : null}
      </div>
      {format === "html" && email.html ? (
        <MessageHtmlFrame html={email.html} title={t("email.htmlBody")} lang={lang} inlineUrls={inlineUrls}
          className="min-h-96 flex-1 rounded-none border-0" />
      ) : email.text ? (
        <div lang={lang} className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 text-sm text-fg">{email.text}</div>
      ) : (
        <EmptyState title={t("email.noBody")} />
      )}
    </div>
  );
}
