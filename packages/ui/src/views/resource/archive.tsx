import * as React from "react";
import { archiveFlagField, fieldUpdatable, refineResourceName, rowPublicId, useModelMetadata, type DataResourceMetadata, type Row } from "@angee/metadata";
import { useUpdate, type BaseRecord, type HttpError } from "@refinedev/core";

import { Glyph } from "../../chrome/Glyph";
import { useUiT } from "../../i18n";
import { SelectionBar } from "../../ui/selection-bar";
import type { ActionDescriptor } from "../page/Action";
import { useBulkAction, type BulkSelectionProps } from "./bulk-action";

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

/** The selection composes the same admission, flag patches and row predicates as the form.
 * @owner SelectionBar
 */
export function BulkArchiveActions(props: BulkSelectionProps): React.ReactElement | null {
  const actions = useArchiveActions(props.resource);
  return actions.length ? <ArchiveSelectionActions {...props} actions={actions} /> : null;
}

function ArchiveSelectionActions({ resource, selectedRows, onSucceeded, actions }: BulkSelectionProps & {
  actions: readonly ActionDescriptor[];
}): React.ReactElement {
  const t = useUiT();
  const bulk = useBulkAction(resource, onSucceeded);
  // This is the record form's applyPatch -> useUpdate transport. The registered
  // provider owns the update root and _set; only settlement moves to the batch.
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
        ids,
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
