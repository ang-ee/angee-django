import * as React from "react";
import { Chip, Column, List, ResourceList } from "@angee/ui";

import { useWebformsT } from "../i18n";
import { FORM_MODEL } from "../resources";
import { undeliveredSummary } from "../undelivered";
import { webformForm } from "./WebformForm";

/**
 * The forms staff manage, with whether each takes submissions, what a response
 * becomes and, while any waits for delivery, how many do and when the first is removed.
 */
export function FormsPage(): React.ReactElement {
  const t = useWebformsT();
  return (
    <ResourceList resource={FORM_MODEL} form={webformForm} placement="inline" routed>
      <List resource={FORM_MODEL} fields={["undelivered_expires_at"]}>
        <Column field="name" />
        <Column field="status" widget="statusBadge" />
        <Column field="target_class" header={t("form.target")} />
        <Column field="undelivered_count" header={t("form.undelivered")} render={(row) => {
          const summary = undeliveredSummary(row, t);
          return summary ? <Chip tone="warning">{summary}</Chip> : null;
        }} />
        <Column field="updated_at" />
      </List>
    </ResourceList>
  );
}
