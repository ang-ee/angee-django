import { ConnectDirectoryAction, type ConnectDirectoryFields } from "@angee/parties";
import type { AuthoredVariables } from "@angee/refine";
import { mutationDialogValueCodecs, type MutationDialogValues } from "@angee/ui";
import * as React from "react";

import { ConnectCardDavDirectory } from "./documents";

const fields: ConnectDirectoryFields = (t) => [
  {
    name: "name",
    label: t("directory.name"),
    placeholder: t("directory.carddav.namePlaceholder"),
    required: true,
  },
  {
    name: "serverUrl",
    label: t("directory.carddav.serverUrl"),
    placeholder: t("directory.carddav.serverUrlPlaceholder"),
    required: true,
  },
  {
    name: "username",
    label: t("directory.carddav.username"),
    required: true,
  },
  {
    name: "password",
    label: t("directory.carddav.password"),
    widget: "password",
    required: true,
  },
];

function parseValues(
  values: MutationDialogValues,
): AuthoredVariables<typeof ConnectCardDavDirectory> {
  return {
    name: mutationDialogValueCodecs.requiredString(values.name, "name"),
    serverUrl: mutationDialogValueCodecs.requiredString(values.serverUrl, "serverUrl"),
    username: mutationDialogValueCodecs.requiredString(values.username, "username"),
    // A password may begin or end with a space, so it is sent as entered.
    password: mutationDialogValueCodecs.verbatimString(values.password, "password"),
  };
}

/** The CardDAV connect verb: one dialog creates the credential and the directory. */
export function ConnectCardDavAction(): React.ReactElement {
  return (
    <ConnectDirectoryAction
      document={ConnectCardDavDirectory}
      fields={fields}
      i18nPrefix="directory.carddav"
      parseValues={parseValues}
    />
  );
}
