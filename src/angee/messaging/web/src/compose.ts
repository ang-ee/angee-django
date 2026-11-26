import * as React from "react";
import {
  AuthoredEnvelopeError,
  failedWriteOutcome,
  useAuthoredMutation,
  useInvalidateAuthoredModels,
  type AuthoredMutateOptions,
  type AuthoredVariables,
  type DocumentData,
  type FailedWriteOutcome,
} from "@angee/refine";
import type { IdempotencyKeyState } from "@angee/ui";

import { MessagingComposeMessage } from "./documents";
import { useMessagingT } from "./i18n";

/** What a composed message changes: its row and its thread, which transcripts and lists read. */
const COMPOSE_MODELS = ["messaging.Message", "messaging.Thread"] as const;

/** The draft of the New message dialog: one new email at a time, from its first Send until it is written. */
export const NEW_EMAIL_SCOPE = "messaging.new-email";

/**
 * The draft of the reply box of the conversation `thread`, from its first Send until it is written:
 * Reply on another email of it retargets the same draft, so a resend after a lost answer carries the
 * same key, and `compose` answers an earlier version it wrote with a conflict rather than writing twice.
 */
export function replyScope(thread: string): string {
  return `messaging.reply:${thread}`;
}

export type ComposeVariables = AuthoredVariables<typeof MessagingComposeMessage>;
export type ComposeData = DocumentData<typeof MessagingComposeMessage>;
export type ComposeMessage = (variables: ComposeVariables, options?: AuthoredMutateOptions) => Promise<ComposeData | undefined>;

/**
 * Write one outgoing message through `compose_message`, as the New message dialog and the
 * reply box send it, each with its draft's idempotency key (`useIdempotencyKeys`). What it
 * changed is read again once it is written (`ok`), never after a refusal. A key an earlier version
 * of the draft spent (`CREATION_KEY_CONFLICT`) says that version was written: it is read again too,
 * and the conflict is told in the page's words and stays the conflict the key's owner reads
 * (`failedWriteOutcome`), so the draft ends and its next send writes the text as it is now.
 */
export function useComposeMessage(): ComposeMessage {
  const t = useMessagingT();
  const invalidate = useInvalidateAuthoredModels();
  const [compose] = useAuthoredMutation(MessagingComposeMessage, {
    invalidateModels: COMPOSE_MODELS,
    shouldInvalidate: (data) => data?.compose_message?.ok === true,
  });
  return React.useCallback<ComposeMessage>(async (variables, options) => {
    try {
      return await compose(variables, options);
    } catch (cause) {
      if (failedWriteOutcome(cause) !== "conflict") throw cause;
      invalidate(COMPOSE_MODELS);
      throw new AuthoredEnvelopeError(t("compose.sentEarlier"), "CREATION_KEY_CONFLICT");
    }
  }, [compose, invalidate, t]);
}

/** What a resolved answer tells the draft's key (`useIdempotencyKeys().send`'s `outcome`): written, or refused in band. */
export function composeOutcome(data: ComposeData | undefined): FailedWriteOutcome | null {
  return data?.compose_message?.ok === true ? null : "refused";
}

/**
 * Whether an earlier attempt of a draft may already have been sent: an attempt's answer was lost,
 * and no conflict has since said that it was. It holds across a closed and reopened surface, where
 * the lost answer's own words did not stay.
 */
export function mayAlreadyBeSent(state: IdempotencyKeyState | null): boolean {
  return Boolean(state?.uncertain) && state?.outcome !== "conflict";
}
