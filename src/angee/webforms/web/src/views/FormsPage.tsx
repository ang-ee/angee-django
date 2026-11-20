import * as React from "react";
import { Column, List, ResourceList } from "@angee/ui";

import { useWebformsT } from "../i18n";
import { FORM_MODEL } from "../resources";
import { webformForm } from "./WebformForm";

/** The forms staff manage, with whether each takes submissions and what a response becomes. */
export function FormsPage(): React.ReactElement {
  const t = useWebformsT();
  return (
    <ResourceList resource={FORM_MODEL} form={webformForm} placement="inline" routed>
      <List resource={FORM_MODEL}>
        <Column field="name" />
        <Column field="status" widget="statusBadge" />
        <Column field="target_class" header={t("form.target")} />
        <Column field="updated_at" />
      </List>
    </ResourceList>
  );
}
