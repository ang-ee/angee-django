import { useMemo, useState, type ReactElement } from "react";
import type { CrudFilter } from "@refinedev/core";

import { useUiT } from "../../i18n";
import { Button } from "../../ui/button";
import { relationIdList, type WidgetField, type WidgetControlProps } from "../../widgets/types";
import { Many2ManyEdit } from "../../widgets/many2many";
import type { RelationFieldInfo } from "../resource/model-metadata-defaults";
import type { RelationCreateConfig } from "./RelationPicker";
import {
  RelationRecordDialog,
  relationCreateTitle,
  type RelationDialogState,
} from "./RelationRecordDialog";
import { relationSelectedOption, useRelationOptions } from "./relation-options";

export interface RelationMultiFieldWidgetProps {
  value?: readonly unknown[] | null;
  /** Receives the picked related records' public ids (the `many2many` cell value). */
  onChange?: (value: readonly unknown[]) => void;
  readOnly?: boolean;
  controlProps?: WidgetControlProps;
  relation: RelationFieldInfo;
  /** Server-side filters narrowing the rows offered by the multi-picker. */
  filters?: readonly CrudFilter[];
  /**
   * Enables inline creation: a visible button (`actionLabel`, else `New <model>`)
   * opens the related model's create form, and the saved record joins the
   * selection. The same config `RelationPicker.create` takes.
   */
  create?: RelationCreateConfig;
  "aria-label"?: string;
}

/**
 * The to-many analog of {@link RelationFieldWidget}: an M2M line cell that fetches
 * the related model's rows once and renders them as a multi-select of chips (the
 * shared `many2many` widget), reading and writing the related records' public ids.
 * `EditableLines` uses it for a `kind: "list"` child field whose relation target
 * resolved (`relationListFieldInfo`); the diff engine serializes the picked ids
 * into the `<resource>_save` line input. Fetches only for an editable cell — a
 * read-only lines view never queries the option list.
 */
export function RelationMultiFieldWidget({
  value,
  onChange,
  readOnly,
  controlProps,
  relation,
  filters,
  create,
  "aria-label": ariaLabel,
}: RelationMultiFieldWidgetProps): ReactElement {
  const t = useUiT();
  const [dialog, setDialog] = useState<RelationDialogState | null>(null);
  const { options, list } = useRelationOptions(relation, {
    enabled: !readOnly,
    filters,
    sort: true,
  });
  const field = useMemo<WidgetField>(
    () => ({
      options: [
        ...options,
        ...(value ?? []).flatMap((record) => {
          const option = relationSelectedOption(record, relation.labelField);
          return option && !options.some((item) => item.value === option.value) ? [option] : [];
        }),
      ],
      label: ariaLabel,
      controlProps,
    }),
    [options, value, relation.labelField, ariaLabel, controlProps],
  );
  const control = (
    <Many2ManyEdit
      value={value ?? []}
      onChange={onChange}
      readOnly={readOnly}
      field={field}
    />
  );
  if (!create || readOnly) return control;
  return (
    <>
      <div className="flex min-w-0 items-start gap-1">
        <div className="min-w-0 flex-1">{control}</div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="shrink-0"
          onClick={() => setDialog({ mode: "create", query: "" })}
        >
          {create.actionLabel ?? relationCreateTitle(create, t)}
        </Button>
      </div>
      <RelationRecordDialog
        dialog={dialog}
        create={create}
        onClose={() => setDialog(null)}
        onCreated={(id) => {
          onChange?.(relationIdList([...(value ?? []), id]));
          list.refetch();
        }}
      />
    </>
  );
}
