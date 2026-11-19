import { ConnectDirectoryAction, type ConnectDirectoryFields } from "@angee/parties";
import type { AuthoredVariables } from "@angee/refine";
import { mutationDialogValueCodecs, type MutationDialogValues } from "@angee/ui";
import * as React from "react";

import { ConnectHubSpotDirectory } from "./documents";

const fields: ConnectDirectoryFields = (t) => [
  {
    name: "name",
    label: t("directory.name"),
    placeholder: t("directory.hubspot.namePlaceholder"),
    required: true,
  },
  {
    name: "token",
    label: t("directory.hubspot.serviceKey"),
    widget: "password",
    required: true,
  },
];

function parseValues(
  values: MutationDialogValues,
): AuthoredVariables<typeof ConnectHubSpotDirectory> {
  return {
    name: mutationDialogValueCodecs.requiredString(values.name, "name"),
    token: mutationDialogValueCodecs.verbatimString(values.token, "token"),
  };
}

/** The HubSpot connect verb: one dialog tests the service key, then creates the credential and the directory. */
export function ConnectHubSpotAction(): React.ReactElement {
  return (
    <ConnectDirectoryAction
      document={ConnectHubSpotDirectory}
      fields={fields}
      i18nPrefix="directory.hubspot"
      parseValues={parseValues}
    />
  );
}
