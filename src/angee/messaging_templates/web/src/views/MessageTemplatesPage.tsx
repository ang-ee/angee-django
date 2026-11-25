import * as React from "react";
import { Column, List, ResourceList } from "@angee/ui";

import { useMessagingTemplatesT } from "../i18n";
import { MESSAGE_TEMPLATE_MODEL, MESSAGE_TEMPLATE_PRESET_IDS } from "../resources";
import { messageTemplateForm } from "./MessageTemplateForm";

/**
 * The message template library: every row in one list, grouped by kind, each
 * opening in the registered form. A row shows whether it is shipped, whether it
 * is effective (what renders for its kind, key and language), and whether its
 * shipped default changed since it was overridden; the presets list those
 * overrides and the effective rows.
 *
 * The record opens in place of the list. Beside it is the intended shape, one
 * placement away (`placement="split"`), once the list's split gives the record a
 * scrolling container as its in-place record has.
 */
export function MessageTemplatesPage(): React.ReactElement {
  const t = useMessagingTemplatesT();
  return (
    <ResourceList resource={MESSAGE_TEMPLATE_MODEL} form={messageTemplateForm} placement="inline" routed>
      <List resource={MESSAGE_TEMPLATE_MODEL} defaultGroups={{ list: { field: "kind" } }}
        presetIds={MESSAGE_TEMPLATE_PRESET_IDS}>
        <Column field="name" header={t("col.name")} />
        <Column field="language" header={t("col.language")} />
        <Column field="is_baseline" header={t("col.shipped")} />
        <Column field="is_effective" header={t("col.effective")} />
        <Column field="has_updated_default" header={t("col.defaultUpdated")} />
      </List>
    </ResourceList>
  );
}
