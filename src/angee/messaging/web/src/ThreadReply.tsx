import * as React from "react";
import { senderDisplayName } from "@angee/parties";
import { extractActionOutcome, failedWriteOutcome, stringValue } from "@angee/refine";
import {
  Banner,
  errorMessage,
  textRoleVariants,
  useActionResultRun,
  useIdempotencyDraftOwner,
  useIdempotencyKeys,
  useIdempotencyKeyState,
  useLatestRef,
  useRuntimeViewAs,
  useToast,
  useUiT,
  useUnsavedChangesNavigationGuard,
  type UnsavedChangesMove,
} from "@angee/ui";

import { composeOutcome, mayAlreadyBeSent, replyScope, useComposeMessage } from "./compose";
import type { ThreadTranscriptRow } from "./documents";
import { useMessagingT } from "./i18n";
import { MessageReplyQuote } from "./MessageReplyQuote";
import { StreamComposer } from "./StreamComposer";

/** The quiet call options: the box tells a failure itself, so refine's generic notice stays off. */
const BOX_RENDERS_FAILURE = { errorNotification: false } as const;

export interface ThreadReplyProps {
  /** The conversation the box writes in: its draft (`replyScope`) whichever of its emails it answers. */
  threadId: string;
  /** The email answered; the answer goes on its thread, to its Reply-To, from its channel. */
  parent: ThreadTranscriptRow;
  /** Which route moves abandon the unsent text: those ask first, every other move keeps it. */
  leaves: (move: UnsavedChangesMove) => boolean;
  /** The answer was written: the box closes and the transcript shows its end. */
  onSent: () => void;
  /** An earlier version of the answer was written: the transcript shows its end, where it is. */
  onShowLatest: () => void;
  /** The person gave the reply up (asked first when it held text). */
  onClose: () => void;
}

/**
 * The reply box docked under a conversation: the quoted email, the channel it goes out from, and
 * the text, which takes focus. Send (Ctrl/Cmd+Enter) answers the email through `compose_message`
 * with the conversation's draft key (`replyScope`), so sending again from this page writes it
 * once, to whichever of its emails the box answers now; a resolved answer is toasted by `useActionResultRun`, a failure is told from what it says
 * (`failedWriteOutcome`): a lost answer, a version written earlier and an attempt that may already
 * have been sent in the box's live note, a refusal in a toast, an unsent request by the
 * connection boundary alone. Under a preview the box is read-only and sends nothing.
 */
export function ThreadReply({ threadId, parent, leaves, onSent, onShowLatest, onClose }: ThreadReplyProps): React.ReactElement {
  const t = useMessagingT();
  const uiT = useUiT();
  const toast = useToast();
  const settle = useActionResultRun();
  const preview = useRuntimeViewAs();
  const readOnly = Boolean(preview.viewAs || preview.pending);
  const keys = useIdempotencyKeys();
  const scope = replyScope(threadId);
  const state = useIdempotencyKeyState(scope);
  const compose = useComposeMessage();
  const [text, setText] = React.useState("");
  const [sending, setSending] = React.useState(false);
  // Re-entry is refused while a send is on its way: a second press would toast its answer twice.
  const sendingRef = React.useRef(false);
  const textRef = useLatestRef(text);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);
  // An answer that lands after the box went away (the person left while it sent) is still reported.
  const mounted = React.useRef(true);
  React.useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const author = senderDisplayName(parent.sender, t("message.author"));
  useIdempotencyDraftOwner(readOnly ? null : scope, `the reply box of conversation ${threadId}`);
  const requestLeave = useUnsavedChangesNavigationGuard({
    isDirty: text.trim() !== "" || sending,
    isDirtyNow: () => textRef.current.trim() !== "" || sendingRef.current,
    readOnly,
    leaves,
    pendingNow: () => sendingRef.current,
  });
  React.useEffect(() => { inputRef.current?.focus(); }, [parent.id]);
  // A draft given up with nothing possibly written ends; one whose answer was lost keeps its key.
  React.useEffect(() => () => keys.discard(scope), [keys, scope]);

  const send = async (): Promise<void> => {
    const body = stringValue(text);
    if (sendingRef.current || readOnly || body === null) return;
    sendingRef.current = true;
    setSending(true);
    try {
      const data = await keys.send(scope, (idempotencyKey) => compose(
        { parent: parent.id, text: body, queued: true, idempotency_key: idempotencyKey }, BOX_RENDERS_FAILURE,
      ), composeOutcome);
      const outcome = await settle(async () => extractActionOutcome(data, "compose_message"));
      if (outcome?.ok && mounted.current) {
        setText("");
        onSent();
      }
    } catch (cause) {
      const told = failedWriteOutcome(cause);
      if (!mounted.current) {
        // Gone, the box's note says nothing: the late report the shared dialogs give (`useLateAnswerReport`'s
        // wording; that owner is not exported yet), and the connection boundary's for an unsent request.
        if (told !== "not-sent") {
          toast.danger({ title: t("compose.notSent"),
            description: told === "unconfirmed" ? uiT("form.answerLost") : errorMessage(cause, t("compose.notSent")) });
        }
        return;
      }
      // The note tells a lost answer and an earlier version; the connection boundary, an unsent request.
      if (told === "refused" || told === "denied") toast.danger({ title: errorMessage(cause, t("compose.notSent")) });
      if (told === "conflict") onShowLatest();
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  };

  const cancel = async (): Promise<void> => {
    if (sendingRef.current || !(await requestLeave())) return;
    onClose();
  };

  // A lost answer says so itself; an earlier attempt that may have been sent is said after any later answer.
  const note = state?.outcome === "unconfirmed" ? uiT("form.answerLostResend")
    : state?.outcome === "conflict" ? t("compose.sentEarlier")
      : mayAlreadyBeSent(state) ? t("compose.replyMayAlreadyBeSent") : null;
  return (
    <section aria-label={t("compose.replyBox", { author })} className="border-t border-border-subtle p-3">
      <StreamComposer
        value={text}
        onChange={setText}
        onSubmit={() => void send()}
        disabled={readOnly || sending}
        ready={text.trim() !== ""}
        submitKey="mod-enter"
        prompt={t("compose.replyPrompt")}
        submitLabel={t("compose.send")}
        readerLine={parent.channel?.display_name ? t("compose.sentFrom", { channel: parent.channel.display_name }) : undefined}
        inputRef={inputRef}
        before={<>
          {readOnly ? <Banner tone="warning">{uiT("viewAs.readOnly")}</Banner> : null}
          <p role="status" className={textRoleVariants({ role: "caption" })}>{note}</p>
          <MessageReplyQuote message={parent} onClear={() => void cancel()} />
        </>}
      />
    </section>
  );
}
