import { useAuthoredMutation } from "@angee/refine";
import * as React from "react";
import { ActionTrigger, MutationDialog, mutationDialogValueCodecs, type DescriptorField, type MutationDialogValues } from "@angee/ui";

import { ConnectCardDavDirectory } from "./documents";
import { useCardDavT } from "./i18n";

const MODEL = "parties.Directory";

/** The CardDAV connect verb: one dialog creates the credential and the directory. */
export function ConnectCardDavAction(): React.ReactElement {
  // Correct as-is: parties.Directory declares changes(Directory, field="directoryChanged").
  const [connect] = useAuthoredMutation(ConnectCardDavDirectory, {
    invalidateModels: [MODEL],
  });
  const t = useCardDavT();
  const fields = React.useMemo<readonly DescriptorField[]>(
    () => [
      {
        name: "name",
        label: t("directory.connect.name"),
        placeholder: t("directory.connect.namePlaceholder"),
        required: true,
      },
      {
        name: "serverUrl",
        label: t("directory.connect.serverUrl"),
        placeholder: t("directory.connect.serverUrlPlaceholder"),
        required: true,
      },
      {
        name: "username",
        label: t("directory.connect.username"),
        required: true,
      },
      {
        name: "password",
        label: t("directory.connect.password"),
        widget: "password",
        required: true,
      },
    ],
    [t],
  );

  return (
    <MutationDialog
      trigger={<ActionTrigger glyph="plus">{t("directory.connect.button")}</ActionTrigger>}
      title={t("directory.connect.title")}
      description={t("directory.connect.description")}
      fields={fields}
      submitLabel={t("directory.connect.submit")}
      submittingLabel={t("directory.connect.submitting")}
      errorFallback={t("directory.connect.error")}
      parseValues={parseDirectoryValues}
      onSubmit={async (values) => ({ status: "ok", data: await connect(values) })}
    />
  );
}

function parseDirectoryValues(values: MutationDialogValues) {
  return {
    name: mutationDialogValueCodecs.requiredString(values.name, "name"),
    serverUrl: mutationDialogValueCodecs.requiredString(
      values.serverUrl,
      "serverUrl",
    ),
    username: mutationDialogValueCodecs.requiredString(values.username, "username"),
    password: mutationDialogValueCodecs.verbatimString(values.password, "password"),
  };
}
