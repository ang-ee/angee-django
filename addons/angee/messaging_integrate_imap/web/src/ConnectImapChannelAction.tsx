import { useAuthoredMutation } from "@angee/refine";
import * as React from "react";
import { Button, Glyph, MutationDialog, type MutationDialogField } from "@angee/ui";

import { ConnectImapChannel } from "./documents";
import { useMessagingImapT } from "./i18n";

const MODEL = "messaging.Channel";

/** Button + dialog contributed into the messaging channel toolbar slot. */
export function ConnectImapChannelAction(): React.ReactElement {
  const t = useMessagingImapT();
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t("channel.connect.button")}
      </Button>
      <ConnectDialog open={open} onOpenChange={setOpen} />
    </>
  );
}

function ConnectDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  // Correct as-is: messaging.Channel declares changes(Channel, field="channelChanged").
  const [connect] = useAuthoredMutation(ConnectImapChannel, {
    invalidateModels: [MODEL],
  });
  const t = useMessagingImapT();
  const fields = React.useMemo<readonly MutationDialogField[]>(
    () => [
      {
        name: "name",
        label: t("channel.connect.name"),
        placeholder: t("channel.connect.namePlaceholder"),
        required: true,
      },
      {
        name: "host",
        label: t("channel.connect.host"),
        placeholder: t("channel.connect.hostPlaceholder"),
        required: true,
      },
      {
        name: "security",
        label: t("channel.connect.security"),
        widget: "select",
        options: [
          { value: "ssl", label: t("channel.connect.securitySsl") },
          { value: "starttls", label: t("channel.connect.securityStarttls") },
          { value: "plain", label: t("channel.connect.securityPlain") },
        ],
        required: true,
      },
      {
        name: "port",
        label: t("channel.connect.port"),
        kind: "integer",
        placeholder: t("channel.connect.portPlaceholder"),
      },
      {
        name: "username",
        label: t("channel.connect.username"),
        required: true,
      },
      {
        name: "password",
        label: t("channel.connect.password"),
        widget: "password",
        required: true,
      },
      {
        name: "mailboxes",
        label: t("channel.connect.mailboxes"),
        widget: "textarea",
        placeholder: t("channel.connect.mailboxesPlaceholder"),
        description: t("channel.connect.mailboxesDescription"),
      },
      {
        name: "ownAddresses",
        label: t("channel.connect.ownAddresses"),
        widget: "textarea",
        placeholder: t("channel.connect.ownAddressesPlaceholder"),
        description: t("channel.connect.ownAddressesDescription"),
      },
    ],
    [t],
  );

  return (
    <MutationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("channel.connect.title")}
      description={t("channel.connect.description")}
      fields={fields}
      initialValues={{ security: "ssl" }}
      submitLabel={t("channel.connect.submit")}
      submittingLabel={t("channel.connect.submitting")}
      cancelLabel={t("channel.connect.cancel")}
      errorFallback={t("channel.connect.error")}
      onSubmit={(values) =>
        connect({
          name: stringValue(values.name).trim(),
          host: stringValue(values.host).trim(),
          security: stringValue(values.security) || "ssl",
          port: numberValue(values.port),
          username: stringValue(values.username).trim(),
          password: stringValue(values.password),
          mailboxes: lineValues(values.mailboxes),
          ownAddresses: lineValues(values.ownAddresses),
        })
      }
    />
  );
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return Math.trunc(parsed);
  }
  return undefined;
}

function lineValues(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => lineValues(String(item)));
  }
  return stringValue(value)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}
