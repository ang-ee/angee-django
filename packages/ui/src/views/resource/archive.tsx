import * as React from "react";
import { archiveFlagField, fieldUpdatable, useModelMetadata, type DataResourceMetadata } from "@angee/metadata";

import { useUiT } from "../../i18n";
import type { ActionDescriptor } from "../page/Action";

/**
 * Archive and Unarchive patch the metadata-owned flag through the form's update
 * binding, only when the registered resource's update root accepts that field.
 * @owner RecordActionBar
 */
export function useArchiveActions(
  resource: DataResourceMetadata | null | undefined,
): readonly ActionDescriptor[] {
  const t = useUiT();
  const metadata = useModelMetadata(resource?.modelLabel ?? "");
  const flag = archiveFlagField(resource);
  const available = flag !== null && fieldUpdatable(metadata, flag);
  return React.useMemo<readonly ActionDescriptor[]>(() => {
    if (!available || flag === null) return [];
    return [
      {
        id: "archive",
        label: t("record.archive.action"),
        icon: "archive",
        placement: "menu",
        set: { [flag]: true },
        visibleWhen: (record) => record[flag] !== true,
      },
      {
        id: "unarchive",
        label: t("record.archive.unarchive"),
        icon: "undo-2",
        placement: "menu",
        set: { [flag]: false },
        visibleWhen: (record) => record[flag] === true,
      },
    ];
  }, [available, flag, t]);
}
