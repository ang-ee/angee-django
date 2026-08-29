import {
  ConnectChannelAction,
  type ConnectChannelFields,
  type MessagingT,
} from "@angee/messaging";
import type { AuthoredVariables } from "@angee/refine";
import {
  mutationDialogValueCodecs,
  type MutationDialogValues,
} from "@angee/ui";
import * as React from "react";

import { ConnectImapChannel } from "./documents";

const fields: ConnectChannelFields = (t) => [
  {
    name: "name",
    label: t("channel.connect.name"),
    placeholder: t("channel.imap.namePlaceholder"),
    required: true,
  },
  {
    name: "host",
    label: t("channel.imap.host"),
    placeholder: t("channel.imap.hostPlaceholder"),
    required: true,
  },
  {
    name: "security",
    label: t("channel.imap.security"),
    widget: "select",
    options: [
      { value: "ssl", label: t("channel.imap.securitySsl") },
      { value: "starttls", label: t("channel.imap.securityStarttls") },
      { value: "plain", label: t("channel.imap.securityPlain") },
    ],
    required: true,
  },
  {
    name: "port",
    label: t("channel.imap.port"),
    kind: "integer",
    placeholder: t("channel.imap.portPlaceholder"),
  },
  {
    name: "username",
    label: t("channel.imap.username"),
    required: true,
  },
  {
    name: "password",
    label: t("channel.imap.password"),
    widget: "password",
    required: true,
  },
  {
    name: "mailboxes",
    label: t("channel.imap.mailboxes"),
    widget: "textarea",
    placeholder: t("channel.imap.mailboxesPlaceholder"),
    description: t("channel.imap.mailboxesDescription"),
  },
  {
    name: "ownAddresses",
    label: t("channel.imap.ownAddresses"),
    widget: "textarea",
    placeholder: t("channel.imap.ownAddressesPlaceholder"),
    description: t("channel.imap.ownAddressesDescription"),
  },
];

function parseValues(
  values: MutationDialogValues,
  t: MessagingT,
): AuthoredVariables<typeof ConnectImapChannel> {
  return {
    name: mutationDialogValueCodecs.requiredString(values.name, "name"),
    host: mutationDialogValueCodecs.requiredString(values.host, "host"),
    security: mutationDialogValueCodecs.requiredString(values.security, "security"),
    port:
      mutationDialogValueCodecs.integer(
        values.port,
        t("channel.imap.port"),
        (label) => t("channel.connect.integerInvalid", { label }),
      ) ?? undefined,
    username: mutationDialogValueCodecs.requiredString(values.username, "username"),
    password: mutationDialogValueCodecs.verbatimString(values.password, "password"),
    mailboxes: lineValues(values.mailboxes),
    ownAddresses: lineValues(values.ownAddresses),
  };
}

/** IMAP field declaration composed through the messaging-owned connect ceremony. */
export function ConnectImapChannelAction(): React.ReactElement {
  return (
    <ConnectChannelAction
      kind="mutation"
      document={ConnectImapChannel}
      fields={fields}
      i18nPrefix="channel.imap"
      initialValues={{ security: "ssl" }}
      parseValues={parseValues}
    />
  );
}

function lineValues(value: unknown): string[] {
  return (mutationDialogValueCodecs.string(value) ?? "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}
