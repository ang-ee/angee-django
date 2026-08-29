import {
  ConnectChannelAction,
  type ConnectChannelFields,
} from "@angee/messaging";
import type { AuthoredVariables } from "@angee/refine";
import {
  mutationDialogValueCodecs,
  type MutationDialogValues,
} from "@angee/ui";
import * as React from "react";

import { ConnectSlackChannel } from "./documents";

const fields: ConnectChannelFields = (t) => [
  {
    name: "name",
    label: t("channel.connect.name"),
    placeholder: t("channel.slack.namePlaceholder"),
    required: true,
  },
  {
    name: "token",
    label: t("channel.slack.token"),
    widget: "password",
    placeholder: t("channel.slack.tokenPlaceholder"),
    required: true,
    description: (
      <>
        <span>{t("channel.slack.tokenHelp")}</span>{" "}
        <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer">
          {t("channel.slack.appsLink")}
        </a>
      </>
    ),
  },
];

function parseValues(
  values: MutationDialogValues,
): AuthoredVariables<typeof ConnectSlackChannel> {
  return {
    name: mutationDialogValueCodecs.requiredString(values.name, "name"),
    token: mutationDialogValueCodecs.requiredString(values.token, "token"),
  };
}

/** Slack field declaration composed through the messaging-owned connect ceremony. */
export function ConnectSlackChannelAction(): React.ReactElement {
  return (
    <ConnectChannelAction
      kind="mutation"
      document={ConnectSlackChannel}
      fields={fields}
      i18nPrefix="channel.slack"
      parseValues={parseValues}
    />
  );
}
