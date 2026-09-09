import { ConditionalMutationButton, isConnectedOrPaused } from "@angee/integrate";
import { useMessagingT } from "@angee/messaging";
import * as React from "react";

/**
 * Re-enter an IMAP channel's username/password in place.
 *
 * The IMAP vendor owns the credential shape (Basic auth) and the
 * `update_imap_channel_credential` verb; the shared conditional button owns
 * the record-chrome dispatch, and its typed-args dialog collects the login and
 * binds the server's in-band refusal. Follow it with the shared Test connection.
 */
export function UpdateImapCredentialAction(): React.ReactElement {
  const t = useMessagingT();
  return (
    <ConditionalMutationButton
      field="update_imap_channel_credential"
      label={t("channel.imap.credential.button")}
      glyph="auth"
      when={isConnectedOrPaused}
      args={[
        { name: "username", label: t("channel.imap.username") },
        { name: "password", label: t("channel.imap.password"), widget: "password" },
      ]}
    />
  );
}
