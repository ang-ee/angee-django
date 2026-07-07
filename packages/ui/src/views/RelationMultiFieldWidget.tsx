import { useMemo, type ReactElement } from "react";

import type { WidgetField } from "../widgets/types";
import { Many2ManyEdit } from "../widgets/many2many";
import type { RelationFieldInfo } from "./model-metadata-defaults";
import { useRelationOptions } from "./relation-options";

export interface RelationMultiFieldWidgetProps {
  value?: readonly unknown[] | null;
  /** Receives the picked related records' public ids (the `many2many` cell value). */
  onChange?: (value: readonly unknown[]) => void;
  readOnly?: boolean;
  relation: RelationFieldInfo;
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
  relation,
  "aria-label": ariaLabel,
}: RelationMultiFieldWidgetProps): ReactElement {
  const { options } = useRelationOptions(relation, {
    enabled: !readOnly,
    sort: true,
  });
  const field = useMemo<WidgetField>(
    () => ({ options, label: ariaLabel }),
    [options, ariaLabel],
  );
  return (
    <Many2ManyEdit
      value={value ?? []}
      onChange={onChange}
      readOnly={readOnly}
      field={field}
    />
  );
}
