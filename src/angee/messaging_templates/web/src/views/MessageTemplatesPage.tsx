import * as React from "react";
import { Column, List, ResourceList } from "@angee/ui";

import { useMessagingTemplatesT } from "../i18n";
import { MESSAGE_TEMPLATE_MODEL, MESSAGE_TEMPLATE_PRESET_IDS } from "../resources";
import { messageTemplateForm } from "./MessageTemplateForm";

/** How the library shows a row's flags: one read-only badge for the three, a fact in words. */
const FLAG_WIDGET = "booleanBadge";

/**
 * The message template library: every row in one list, grouped by kind, each
 * opening in the registered form. A row shows whether it is shipped, whether it
 * is effective (what renders for its kind, key and language), and whether its
 * shipped default changed since it was overridden; the presets list those
 * overrides and the effective rows.
 *
 * The record opens in place of the list. Beside it is the intended shape: split
 * waits for the shared list's side of a split, which overflows its pane and shows
 * no empty state while no row is open; once those land, the page takes
 * `placement="split"` with `splitLayout={{ stackBelow: 720 }}`, which stacks the
 * record under the list on a narrow screen.
 */
export function MessageTemplatesPage(): React.ReactElement {
  const t = useMessagingTemplatesT();
  // The badge's words for each state; without them it would say an untranslated True or False.
  const flagOptions = React.useMemo(() => [
    { value: "true", label: t("flag.yes") },
    { value: "false", label: t("flag.no") },
  ], [t]);
  return (
    <ResourceList resource={MESSAGE_TEMPLATE_MODEL} form={messageTemplateForm} placement="inline" routed>
      <List resource={MESSAGE_TEMPLATE_MODEL} defaultGroups={{ list: { field: "kind" } }}
        presetIds={MESSAGE_TEMPLATE_PRESET_IDS}>
        <Column field="name" header={t("col.name")} />
        <Column field="language" header={t("col.language")} />
        {/* The three flags read alike: the server gives is_baseline a switch and the derived two none. */}
        <Column field="is_baseline" header={t("col.shipped")} widget={FLAG_WIDGET} options={flagOptions} />
        <Column field="is_effective" header={t("col.effective")} widget={FLAG_WIDGET} options={flagOptions} />
        <Column field="has_updated_default" header={t("col.defaultUpdated")} widget={FLAG_WIDGET}
          options={flagOptions} />
      </List>
    </ResourceList>
  );
}
