import {
  ConditionalMutationButton,
  integrationLifecycleIs,
} from "@angee/integrate";
import { useMessagingT } from "@angee/messaging";
import * as React from "react";

/** Establish the paused channel's future-only delivery boundary before resuming. */
export function PrepareImapNewMailAction(): React.ReactElement {
  const t = useMessagingT();
  return (
    <ConditionalMutationButton
      field="prepare_imap_new_mail"
      label={t("channel.imap.newMail.button")}
      glyph="forward"
      when={integrationLifecycleIs("paused")}
      confirm={{
        title: t("channel.imap.newMail.confirm.title"),
        body: t("channel.imap.newMail.confirm.body"),
        danger: true,
      }}
    />
  );
}
