import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Button,
  Glyph,
  MutationDialog,
  actionFormSubmitResult,
  mutationDialogValueCodecs,
  useIdempotencyKeyState,
  type DescriptorField,
  type MutationDialogValues,
} from "@angee/ui";

import { NEW_EMAIL_SCOPE, mayAlreadyBeSent, useComposeMessage, type ComposeVariables } from "./compose";
import { CHANNEL_MODEL, MessagingSendableChannels } from "./documents";
import { useMessagingT } from "./i18n";

/** The server's `sendable` filter (a refine filter): the channels the viewer may send a new email on. */
const SENDABLE = [{ field: "sendable", operator: "eq", value: true }] as const;
const CHANNEL_MODELS = [CHANNEL_MODEL];

export interface NewMessageActionProps {
  /** Called with the new message's id once it is written: a host opens it. */
  onSent?: (id: string) => void;
}

/** The addresses one of To, Cc and Bcc holds as its chips: trimmed and distinct, in order (the `tagInput` widget's own). */
function addresses(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];
}

/**
 * What the dialog sends: the channel, the chips of To, Cc and Bcc as typed, the subject when
 * there is one, the message, and Send later's time with its offset; it goes out now when there is
 * no time. A list left empty and a blank subject are not sent. The server judges every address,
 * the bound and the time.
 */
export function newMessageVariables(values: MutationDialogValues): Omit<ComposeVariables, "idempotency_key"> {
  const scheduledAt = mutationDialogValueCodecs.datetime(values.scheduled_at);
  const subject = mutationDialogValueCodecs.string(values.subject);
  const recipients = Object.fromEntries((["to", "cc", "bcc"] as const)
    .map((role) => [role, addresses(values[role])] as const)
    .filter(([, entries]) => entries.length > 0));
  return {
    channel: mutationDialogValueCodecs.requiredString(values.channel, "channel"),
    ...recipients,
    ...(subject === null ? {} : { subject }),
    text: mutationDialogValueCodecs.requiredString(values.text, "text"),
    queued: scheduledAt === null,
    ...(scheduledAt === null ? {} : { scheduled_at: scheduledAt }),
  };
}

/**
 * New message: a new email from a channel the viewer may send on. Shown only to a viewer the
 * server lists such a channel for (`sendable`); with one, the dialog's Channel is that one, fixed,
 * and with several the shared relation picker offers them. Every judgement past the presence of
 * a channel and a message is the server's, shown under the field it names. The dialog holds the
 * draft's idempotency key (`NEW_EMAIL_SCOPE`), so sending again from this page writes it once.
 * Once open it stays mounted until it closes, whatever the channels read says meanwhile.
 */
export function NewMessageAction({ onSent }: NewMessageActionProps): React.ReactElement | null {
  const t = useMessagingT();
  const query = useAuthoredQuery(MessagingSendableChannels, undefined, { models: CHANNEL_MODELS });
  const channels = query.data?.channels;
  const limit = query.data?.message_recipient_limit;
  const only = channels?.length === 1 ? channels[0] : undefined;
  const compose = useComposeMessage();
  const state = useIdempotencyKeyState(NEW_EMAIL_SCOPE);
  const uncertain = mayAlreadyBeSent(state);
  const fields = React.useMemo<readonly DescriptorField[]>(() => [
    {
      name: "channel", label: t("compose.channel"), description: t("compose.channelHint"),
      required: true, readOnly: only !== undefined,
      relation: { resource: CHANNEL_MODEL, labelField: "display_name", filters: SENDABLE },
    },
    {
      name: "to", label: t("compose.to"), widget: "tagInput",
      description: limit === undefined ? undefined : t("compose.toHint", { count: limit }),
    },
    { name: "cc", label: t("compose.cc"), widget: "tagInput" },
    { name: "bcc", label: t("compose.bcc"), widget: "tagInput" },
    { name: "subject", label: t("compose.subject"), widget: "text" },
    { name: "text", label: t("compose.message"), widget: "textarea", required: true },
    // A resend of an attempt that may have been written replays it as it was, its timing included.
    { name: "scheduled_at", label: t("compose.sendLater"), widget: "datetime",
      description: t(uncertain ? "compose.sendLaterKeptHint" : "compose.sendLaterHint") },
  ], [limit, only, t, uncertain]);
  const initialValues = React.useMemo(() => (only ? { channel: only.id } : undefined), [only]);
  return (
    <MutationDialog
      trigger={channels?.length ? (
        <Button size="sm" variant="secondary"><Glyph decorative name="send" />{t("compose.newMessage")}</Button>
      ) : undefined}
      title={t("compose.newMessage")}
      description={<span role="status">{uncertain ? t("compose.mayAlreadyBeSent") : null}</span>}
      fields={fields}
      initialValues={initialValues}
      submitLabel={t("compose.send")}
      submittingLabel={t("compose.sending")}
      errorFallback={t("compose.notSent")}
      size="lg"
      parseValues={newMessageVariables}
      idempotencyScope={NEW_EMAIL_SCOPE}
      mutation={(variables, options, idempotencyKey) => compose({ ...variables, idempotency_key: idempotencyKey }, options)}
      mutationResult={(data) => actionFormSubmitResult(data, "compose_message")}
      onSubmitted={(outcome) => { if (outcome.id) onSent?.(outcome.id); }}
    />
  );
}
