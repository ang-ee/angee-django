import { useMemo, type ReactElement } from "react";

import {
  useModelMetadata,
  useModelRoute,
  useResourceList,
  type Row,
} from "@angee/sdk";

import { recordPath } from "./DataPageRouted";
import {
  formFieldsFromMetadata,
  type RelationFieldInfo,
} from "./model-metadata-defaults";
import { RelationPicker } from "./RelationPicker";

// Relation pickers list a bounded set; a record beyond this is found by search
// once the option set is paged (a later refinement), not silently dropped here.
const RELATION_OPTION_LIMIT = 200;

export interface RelationFieldWidgetProps {
  value?: string | null;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  relation: RelationFieldInfo;
  placeholder?: string;
  "aria-label"?: string;
}

/**
 * The auto-wired relational form control: fetches the related model's records,
 * renders a searchable `RelationPicker`, and — when the related model has a
 * create mutation — offers in-place create with fields derived from its
 * metadata. `FormView` resolves the relation target (model, display field,
 * create) from the SDL and renders this for every object-relation field.
 */
export function RelationFieldWidget({
  value,
  onChange,
  readOnly,
  relation,
  placeholder,
  "aria-label": ariaLabel,
}: RelationFieldWidgetProps): ReactElement {
  const list = useResourceList(relation.model, {
    fields: [relation.labelField],
    pageSize: RELATION_OPTION_LIMIT,
  });
  const options = useMemo(
    () =>
      list.rows.map((row: Row) => ({
        value: String(row.id ?? ""),
        label: String(row[relation.labelField] ?? row.id ?? ""),
      })),
    [list.rows, relation.labelField],
  );

  const relatedMetadata = useModelMetadata(relation.model);
  const createFields = useMemo(
    () => formFieldsFromMetadata(relatedMetadata),
    [relatedMetadata],
  );

  // A "follow" arrow appears only when the related model has a routed detail page
  // and a record is selected — navigating to it turns the relation into a link.
  const basePath = useModelRoute(relation.model);
  const followHref = basePath && value ? recordPath(basePath, value) : undefined;

  return (
    <RelationPicker
      value={value}
      onChange={onChange}
      options={options}
      readOnly={readOnly}
      placeholder={placeholder}
      aria-label={ariaLabel}
      followHref={followHref}
      create={
        relation.canCreate && createFields.length > 0
          ? {
              model: relation.model,
              fields: createFields,
              prefillField: relation.labelField,
            }
          : undefined
      }
      onCreated={() => list.refetch()}
      // Edit is offered whenever the model has editable fields — intentionally
      // UX-only, not gated on a `canEdit` flag (the SDL exposes no per-relation
      // edit capability). The server is the authorization boundary: a denied
      // patch surfaces in the dialog's own error banner.
      edit={
        createFields.length > 0
          ? { model: relation.model, fields: createFields }
          : undefined
      }
      onEdited={() => list.refetch()}
    />
  );
}
