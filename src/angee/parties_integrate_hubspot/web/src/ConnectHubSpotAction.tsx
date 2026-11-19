import { useAuthoredMutation } from "@angee/refine";
import * as React from "react";
import { ActionTrigger, MutationDialog, mutationDialogValueCodecs, type DescriptorField, type MutationDialogValues } from "@angee/ui";

import { ConnectHubSpotDirectory } from "./documents";
import { useHubSpotT } from "./i18n";

const MODEL = "parties.Directory";

/** The HubSpot connect verb: one dialog tests the service key, then creates the credential and the directory. */
export function ConnectHubSpotAction(): React.ReactElement {
  // Correct as-is: parties.Directory declares changes(Directory, field="directoryChanged").
  const [connect] = useAuthoredMutation(ConnectHubSpotDirectory, {
    invalidateModels: [MODEL],
  });
  const t = useHubSpotT();
  const fields = React.useMemo<readonly DescriptorField[]>(
    () => [
      {
        name: "name",
        label: t("directory.connect.name"),
        placeholder: t("directory.connect.namePlaceholder"),
        required: true,
      },
      {
        name: "token",
        label: t("directory.connect.token"),
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
      mutation={connect}
    />
  );
}

function parseDirectoryValues(values: MutationDialogValues) {
  return {
    name: mutationDialogValueCodecs.requiredString(values.name, "name"),
    token: mutationDialogValueCodecs.verbatimString(values.token, "token"),
  };
}
