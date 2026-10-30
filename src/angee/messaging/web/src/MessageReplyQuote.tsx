import type { ReactElement } from "react";
import { senderDisplayName, type SenderIdentity } from "@angee/parties";
import { Button, Glyph, cn, textRoleVariants, useInAppLink } from "@angee/ui";
import { useMessagingT } from "./i18n";

/** One reply quote for readers, transcripts and the record-discussion composer. */
export function MessageReplyQuote({ message, href, onOpen, onClear, className }: {
  message: { preview: string; message_type?: string | null; sender?: SenderIdentity | null };
  href?: string;
  onOpen?: () => void;
  onClear?: () => void;
  className?: string;
}): ReactElement {
  const t = useMessagingT();
  const link = useInAppLink(href);
  const author = senderDisplayName(message.sender,
    t(message.message_type === "NOTIFICATION" ? "message.kindNote" : "message.kindMessage"));
  const content = <>
    <Glyph decorative name="quote" className="shrink-0 text-fg-muted" />
    <span className="min-w-0 flex-1">
      <span className={cn("block font-medium", textRoleVariants({ role: "caption" }))}>
        {t("message.replyingTo", { kind: author })}
      </span>
      <span className="block line-clamp-3 whitespace-pre-wrap text-13 text-fg-muted">{message.preview}</span>
    </span>
  </>;
  return <div className={cn("flex items-center gap-2 rounded-6 border-l-2 border-border-subtle bg-surface px-2 py-1", className)}>
    {href || onOpen ? <Button variant="ghost" size="sm" className="h-auto min-w-0 flex-1 justify-start whitespace-normal text-left"
      nativeButton={!href} role={href ? "link" : undefined}
      onClick={onOpen} render={href ? <a href={href} {...link} /> : undefined}>{content}</Button>
      : <div className="flex min-w-0 flex-1 items-center gap-2">{content}</div>}
    {onClear ? <Button type="button" variant="ghost" size="iconSm" aria-label={t("composer.cancelReply")} onClick={onClear}>
      <Glyph name="x" />
    </Button> : null}
  </div>;
}
