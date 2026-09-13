import * as React from "react";
import { useCan, useUpdate, type BaseRecord, type HttpError } from "@refinedev/core";
import {
  fieldUpdatable,
  refineResourceName,
  useModelMetadata,
  useSchemaFieldMetadata,
  type ModelMetadata,
  type Row,
  type SchemaFieldMetadata,
} from "@angee/metadata";
import type { ActionOutcome } from "@angee/refine";

import { Glyph } from "../../chrome/Glyph";
import { errorMessage } from "../../feedback";
import { useUiT } from "../../i18n";
import { titleCase } from "../../lib/titleCase";
import { useLatestRef } from "../../lib/use-latest-ref";
import { DropdownMenu } from "../../ui/dropdown-menu";
import { SelectionBar } from "../../ui/selection-bar";
import { ActionFormDialog } from "../form/ActionFormDialog";
import type { ActionArg, ActionDescriptor, ColumnDescriptor } from "../page";
import { fieldsWithMetadataDefaults, relationFieldInfo } from "./model-metadata-defaults";

export interface BulkEditMenuProps<TRow extends Row> {
  resource: string;
  /** Columns the selection may edit; only those whose field the update root accepts are offered. */
  columns: readonly ColumnDescriptor<TRow>[];
  selectedIds: ReadonlySet<string>;
  /** Called once every selected row was patched — e.g. to clear the selection. */
  onSucceeded: () => void;
  /** Replace the selection with the rows a partial failure left unpatched. */
  onNarrowSelection: (ids: readonly string[]) => void;
}

/**
 * Generic bulk edit for a list selection: a menu of the visible columns the
 * resource's update root accepts. Each opens the shared typed-args action form for
 * that one field, then patches every selected row through the resource's `update`
 * root, one row at a time as bulk delete does. When any row is rejected the form
 * stays open with the first failure and the selection narrows to the rejected rows,
 * so the next submit retries only those.
 */
export function BulkEditMenu<TRow extends Row>({
  resource,
  columns,
  selectedIds,
  onSucceeded,
  onNarrowSelection,
}: BulkEditMenuProps<TRow>): React.ReactElement | null {
  const t = useUiT();
  const metadata = useModelMetadata(resource);
  const schemaMetadata = useSchemaFieldMetadata();
  const dataResource = metadata?.resource ?? null;
  const refineResource = dataResource ? refineResourceName(dataResource) : undefined;
  const editAccess = useCan({
    resource: refineResource,
    action: "edit",
    queryOptions: { enabled: Boolean(refineResource) },
  });
  const update = useUpdate<BaseRecord, HttpError, Record<string, unknown>>({
    resource: refineResource ?? "__angee_disabled__",
    dataProviderName: dataResource?.schemaName,
    invalidates: ["list", "many", "detail"],
    successNotification: false,
    errorNotification: false,
  });
  const { mutateAsync } = update;
  const [formAction, setFormAction] = React.useState<ActionDescriptor | null>(null);
  const selectedIdList = React.useMemo(() => [...selectedIds], [selectedIds]);
  const latestSelectedIds = useLatestRef(selectedIdList);
  const actions = React.useMemo(
    () =>
      bulkEditArgs(columns, metadata, schemaMetadata).map((arg): ActionDescriptor => ({
        id: `bulk-edit-${arg.name}`,
        label: t("bulkEdit.set", { field: typeof arg.label === "string" ? arg.label : titleCase(arg.name) }),
        args: [arg],
        submit: async (values): Promise<ActionOutcome> => {
          const ids = latestSelectedIds.current;
          const value = patchValue(arg, values[arg.name]);
          const results = await Promise.allSettled(
            ids.map((id) => mutateAsync({ id, values: { [arg.name]: value } })),
          );
          const failedIds = ids.filter((_, index) => results[index]?.status === "rejected");
          if (failedIds.length === 0) return { ok: true, message: t("bulkEdit.updated", { count: ids.length }) };
          const failure = results.find((result): result is PromiseRejectedResult => result.status === "rejected");
          onNarrowSelection(failedIds);
          return {
            ok: false,
            message: t("bulkEdit.partial", {
              updated: ids.length - failedIds.length,
              count: ids.length,
              reason: errorMessage(failure?.reason, t("bulkEdit.failed")),
            }),
          };
        },
      })),
    [columns, latestSelectedIds, metadata, mutateAsync, onNarrowSelection, schemaMetadata, t],
  );
  if (editAccess.data?.can === false || actions.length === 0) return null;

  return (
    <>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger
          render={
            <SelectionBar.Action surface="brand">
              <Glyph name="pencil" />
              {t("selection.edit")}
            </SelectionBar.Action>
          }
        />
        <DropdownMenu.Portal>
          <DropdownMenu.Positioner sideOffset={6} align="start">
            <DropdownMenu.Content className="w-52">
              {actions.map((action) => (
                <DropdownMenu.Item key={action.id} onClick={() => setFormAction(action)}>
                  {action.label}
                </DropdownMenu.Item>
              ))}
            </DropdownMenu.Content>
          </DropdownMenu.Positioner>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
      {formAction ? (
        <ActionFormDialog
          key={formAction.id}
          action={formAction}
          context={{ record: null, selectedIds: selectedIdList }}
          open
          onOpenChange={(open) => {
            if (!open) setFormAction(null);
          }}
          onSucceeded={onSucceeded}
        />
      ) : null}
    </>
  );
}

/** One action arg per column whose field the resource's update root accepts. */
function bulkEditArgs<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
  schemaMetadata: SchemaFieldMetadata,
): ActionArg[] {
  if (!metadata) return [];
  const names = [...new Set(columns.flatMap((column) => editableFieldName(column.field, metadata) ?? []))];
  return names
    .filter((name) => fieldUpdatable(metadata, name))
    .map((name): ActionArg => {
      const field = metadata.fields[name];
      const [descriptor] = fieldsWithMetadataDefaults([{ name }], metadata);
      const optional = field?.nullable === true;
      const relation = relationFieldInfo(name, metadata, schemaMetadata);
      if (relation) {
        return { argKind: "relation", name, label: descriptor?.label, resource: relation.resource, optional };
      }
      return {
        name,
        label: descriptor?.label,
        optional,
        ...(descriptor?.widget ? { widget: descriptor.widget } : {}),
        ...(descriptor?.kind ? { kind: descriptor.kind } : {}),
        ...(descriptor?.options ? { options: descriptor.options } : {}),
        ...(descriptor?.currencyField ? { currencyField: descriptor.currencyField } : {}),
      };
    });
}

/**
 * The model field a column edits: its own field, or the to-one relation whose label
 * path a relation column resolves to (`project.name` edits `project`).
 */
function editableFieldName(path: string, metadata: ModelMetadata): string | null {
  if (metadata.fields[path]) return path;
  const [relation, leaf, ...rest] = path.split(".");
  if (!relation || leaf === undefined || rest.length > 0) return null;
  return metadata.fields[relation]?.kind === "relation" ? relation : null;
}

/** An optional arg left empty clears its field instead of writing an empty string. */
function patchValue(arg: ActionArg, value: unknown): unknown {
  return arg.optional === true && (value === "" || value === undefined) ? null : value;
}
