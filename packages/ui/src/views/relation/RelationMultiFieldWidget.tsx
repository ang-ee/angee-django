import { useMemo, useState, type ReactElement } from "react";
import type { CrudFilter } from "@refinedev/core";

import { Glyph } from "../../chrome/Glyph";
import { useUiT } from "../../i18n";
import { Button } from "../../ui/button";
import {
  optionTextLabel,
  relationIdList,
  type WidgetField,
  type WidgetFocusTarget,
} from "../../widgets/types";
import { Many2ManyCellEdit, Many2ManyEdit } from "../../widgets/many2many";
import type { RelationFieldInfo } from "../resource/model-metadata-defaults";
import type { RelationCreateConfig } from "./RelationPicker";
import { RecordReferenceChips } from "./RecordReference";
import {
  RelationRecordDialog,
  relationCreateTitle,
  useRelationForms,
  type RelationDialogState,
} from "./RelationRecordDialog";
import { relationSelectedOption, useRelationOptions } from "./relation-options";

export interface RelationMultiFieldWidgetProps {
  /** Related records (`{ id, … }`) or their public ids, in any mix. */
  value?: readonly unknown[] | null;
  /** Receives the picked related records' public ids (the `many2many` cell value). */
  onChange?: (value: readonly unknown[]) => void;
  /** Called after each completed pick, removal or inline create. */
  onCommit?: () => void;
  readOnly?: boolean;
  /** Compact picker presentation for editable table cells only. */
  compact?: boolean;
  relation: RelationFieldInfo;
  /** Server-side filters narrowing the rows offered by the multi-picker. */
  filters?: readonly CrudFilter[];
  /**
   * Explicit inline-create configuration. Overrides the default derived from the
   * related model's metadata, exactly as `RelationFieldWidget` does; pass null to
   * offer no create. A visible button (`actionLabel`, else `New <model>`) opens
   * the related model's create form, and the saved record joins the selection.
   */
  create?: RelationCreateConfig | null;
  "aria-label"?: string;
  controlRef?: (target: WidgetFocusTarget | null) => void;
}

/**
 * The to-many analog of {@link RelationFieldWidget}: it fetches the related
 * model's rows and renders them as a multi-select of chips (the shared
 * `many2many` widget), reading related records or ids and writing the related
 * records' public ids. Read-only values are linked chips that follow each
 * record's route, and never query the option list. `FormView` and
 * `EditableLines` use it for a `kind: "list"` field whose relation target
 * resolved (`relationListFieldInfo`).
 */
export function RelationMultiFieldWidget({
  value,
  onChange,
  onCommit,
  readOnly,
  compact = false,
  relation,
  filters,
  create,
  "aria-label": ariaLabel,
  controlRef,
}: RelationMultiFieldWidgetProps): ReactElement {
  const t = useUiT();
  const [dialog, setDialog] = useState<RelationDialogState | null>(null);
  const forms = useRelationForms(relation, create);
  const { options, list } = useRelationOptions(relation, {
    enabled: !readOnly,
    filters,
    sort: true,
  });
  // Loaded related records carry their own labels, also outside the option page.
  const selectedOptions = useMemo(
    () => (value ?? []).flatMap((record) => {
      const option = relationSelectedOption(record, relation.labelField);
      return option ? [option] : [];
    }),
    [value, relation.labelField],
  );
  const field = useMemo<WidgetField>(
    () => ({
      options: [
        ...options,
        ...selectedOptions.filter((option) => !options.some((item) => item.value === option.value)),
      ],
      label: ariaLabel,
    }),
    [options, selectedOptions, ariaLabel],
  );
  if (readOnly) {
    return <RecordReferenceChips model={relation.resource} records={relationIdList(value).map((id) => ({
      id, label: selectedOptions.find((option) => option.value === id)?.label,
    }))} />;
  }
  const change = (next: readonly unknown[]): void => {
    onChange?.(next);
    onCommit?.();
  };
  const Edit = compact ? Many2ManyCellEdit : Many2ManyEdit;
  const control = <Edit value={value ?? []} onChange={change} field={field} controlRef={controlRef} />;
  if (!forms.create) return control;
  const createLabel = forms.create.actionLabel ?? relationCreateTitle(forms.create, t);
  return (
    <>
      <div className="flex min-w-0 items-start gap-1">
        <div className="min-w-0 flex-1">{control}</div>
        {compact ? (
          <Button type="button" variant="ghost" size="iconSm" className="shrink-0"
            aria-label={optionTextLabel(createLabel, t("actions.create"))}
            onClick={() => setDialog({ mode: "create", query: "" })}>
            <Glyph decorative name="plus" />
          </Button>
        ) : (
          <Button type="button" variant="secondary" size="sm" className="shrink-0"
            onClick={() => setDialog({ mode: "create", query: "" })}>
            {createLabel}
          </Button>
        )}
      </div>
      <RelationRecordDialog
        dialog={dialog}
        create={forms.create}
        onClose={() => setDialog(null)}
        onCreated={(id) => {
          change(relationIdList([...(value ?? []), id]));
          list.refetch();
        }}
      />
    </>
  );
}
