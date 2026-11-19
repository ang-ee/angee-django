import * as React from "react";
import { allowsRecordPermission, resourceLifecycle, refineResourceName, rowPublicId, type ModelMetadata, type Row } from "@angee/metadata";
import { useUpdate, type BaseRecord, type HttpError } from "@refinedev/core";

import { Glyph } from "../../chrome/Glyph";
import { useUiT } from "../../i18n";
import { SelectionBar } from "../../ui/selection-bar";
import type { ActionDescriptor } from "../page/Action";
import type { BulkSelectionProps } from "./bulk-action";

/**
 * Archive and Unarchive patch the metadata-owned flag through the form's update
 * binding, only when the registered resource's update root accepts that field.
 * @owner RecordActionBar
 */
export function useArchiveActions(
  metadata: ModelMetadata | null | undefined,
): readonly ActionDescriptor[] {
  const t = useUiT();
  const flag = resourceLifecycle(metadata?.resource, metadata).archiveFlag;
  // Without a projection, only resources that declare no permissions admit write.
  const requiresWrite = !allowsRecordPermission(null, "write", metadata);
  return React.useMemo<readonly ActionDescriptor[]>(() => {
    if (flag === null) return [];
    return [
      {
        id: "archive",
        label: t("record.archive.action"),
        icon: "archive",
        placement: "menu",
        permission: requiresWrite ? "write" : undefined,
        set: { [flag]: true },
        visibleWhen: (record) => record[flag] === false,
      },
      {
        id: "unarchive",
        label: t("record.archive.unarchive"),
        icon: "undo-2",
        placement: "menu",
        permission: requiresWrite ? "write" : undefined,
        set: { [flag]: false },
        visibleWhen: (record) => record[flag] === true,
      },
    ];
  }, [requiresWrite, flag, t]);
}

/** The selection composes resource admission, flag patches and state predicates;
 * the record bar alone applies each descriptor's projected permission.
 * @owner SelectionBar
 */
export function BulkArchiveActions(props: BulkSelectionProps): React.ReactElement | null {
  const actions = useArchiveActions(props.modelMetadata);
  return actions.length ? <ArchiveSelectionActions {...props} actions={actions} /> : null;
}

function ArchiveSelectionActions({ modelMetadata, selectedRows, unprocessedCount, bulk, actions }: BulkSelectionProps & {
  actions: readonly ActionDescriptor[];
}): React.ReactElement {
  const t = useUiT();
  const resource = modelMetadata.resource;
  // This is the record form's applyPatch -> useUpdate transport. The registered
  // provider owns the update root and _set. A flag-only bulk write deliberately
  // omits expected_revision; it does not submit the form's observed edit baseline.
  const update = useUpdate<BaseRecord & Row, HttpError, Record<string, unknown>>({
    resource: refineResourceName(resource),
    dataProviderName: resource.schemaName,
    meta: { fields: ["id"] },
    mutationMode: "pessimistic",
    invalidates: [],
    successNotification: false,
    errorNotification: false,
  });
  return <>{actions.map((action) => {
    const ids = selectedRows.flatMap((row) => {
      const id = rowPublicId(row);
      return id && action.visibleWhen?.(row) ? [id] : [];
    });
    if (!ids.length) return null;
    return <SelectionBar.Action key={action.id} surface="brand" pending={bulk.pending} disabled={bulk.blocked}
      onClick={() => void bulk.run({
        ids, unprocessedCount,
        perform: async (id) => {
          const result = await update.mutateAsync({ id, values: action.set! });
          if (!rowPublicId(result.data)) throw new Error(t("action.noResult"));
        },
        done: (count) => t("record.archive.bulkDone", { count }),
        failed: (count) => t("record.archive.bulkFailed", { count }),
      })}>
      <Glyph name={action.icon ?? "archive"} />{action.label}
    </SelectionBar.Action>;
  })}</>;
}
