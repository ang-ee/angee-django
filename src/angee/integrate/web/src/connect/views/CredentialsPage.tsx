import { Column, List, ResourceList } from "@angee/ui";
import * as React from "react";

import { credentialCreateForm } from "../credential-form";

const MODEL = "integrate.Credential";

const credentialList = (
  <List resource={MODEL}>
    <Column field="display_name" />
    <Column field="kind" />
    <Column field="status" widget="statusBadge" />
    <Column field="expires_at" />
    <Column field="last_refresh_at" />
  </List>
);

/** Credential health list with one registered form for routed and inline use. */
export function CredentialsPage(): React.ReactElement {
  return (
    <ResourceList
      resource={MODEL}
      form={credentialCreateForm}
      placement="inline"
      routed
    >
      {credentialList}
    </ResourceList>
  );
}
