import * as React from "react";
import { Column, List, ResourceList } from "@angee/ui";

import { useMessagingTemplatesT } from "../i18n";
import { MESSAGE_TEMPLATE_MODEL, MESSAGE_TEMPLATE_PRESET_IDS } from "../resources";
import { messageTemplateForm } from "./MessageTemplateForm";

/** The library's width beside the open record, kept per browser. */
const SPLIT_LAYOUT = { autoSave: "messaging_templates.library", primarySize: 36, contentMinSize: 45 };

/**
 * The message template library: every row in one list, grouped by kind, with
 * the open row's registered form beside it. A row shows whether it is shipped,
 * whether it is what renders, and whether its shipped default changed since it
 * was overridden; the presets list those overrides and the rows that render.
 */
export function MessageTemplatesPage(): React.ReactElement {
  const t = useMessagingTemplatesT();
  return (
    <ResourceList resource={MESSAGE_TEMPLATE_MODEL} form={messageTemplateForm} placement="split" routed
      splitLayout={SPLIT_LAYOUT}>
      <List resource={MESSAGE_TEMPLATE_MODEL} defaultGroups={{ list: { field: "kind" } }}
        presetIds={MESSAGE_TEMPLATE_PRESET_IDS}>
        <Column field="name" header={t("col.name")} />
        <Column field="language" header={t("col.language")} />
        <Column field="is_baseline" header={t("col.shipped")} />
        <Column field="is_effective" header={t("col.renders")} />
        <Column field="has_updated_default" header={t("col.defaultUpdated")} />
      </List>
    </ResourceList>
  );
}
