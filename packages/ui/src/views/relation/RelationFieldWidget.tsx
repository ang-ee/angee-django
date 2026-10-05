import { useMemo, type ReactElement, type Ref } from "react";
import type { CrudFilter } from "@refinedev/core";

import {
  useResourceRecordHref,
} from "../../runtime";
import { useModelMetadata } from "@angee/metadata";

import type { RelationOption } from "../../widgets/RelationField";
import type { WidgetControlProps } from "../../widgets/types";
import {
  formFieldsFromMetadata,
  type RelationFieldInfo,
} from "../resource/model-metadata-defaults";
import { RelationPicker, type RelationCreateConfig } from "./RelationPicker";
import { useRelationPickerOptions } from "./relation-options";
import { RecordReference } from "./RecordReference";

export interface RelationFieldWidgetProps {
  value?: string | null;
  onChange?: (value: string) => void;
  onCommit?: () => void;
  readOnly?: boolean;
  relation: RelationFieldInfo;
  /** Server-side filters narrowing the rows offered by this relation picker. */
  filters?: readonly CrudFilter[];
  /** Hasura condition supplied by the field's owner; combined with native search filters. */
  where?: Record<string, unknown>;
  /**
   * Explicit inline-create configuration for the picker. Overrides the default
   * derived from the related model's metadata (offered when it has a create
   * mutation and form fields). Pass null to disable creation explicitly.
   */
  create?: RelationCreateConfig | null;
  searchFields?: readonly string[];
  /**
   * The already-loaded selected record as a picker option (id + folded label),
   * derived by `FormView` from the parent read. Shows the trigger label before
   * — and without — the option list ever loading, so a read-only/show view
   * never fires the option query; the freshly fetched label wins once loaded.
   */
  selectedOption?: RelationOption;
  placeholder?: string;
  "aria-label"?: string;
  controlRef?: Ref<HTMLButtonElement>;
  controlProps?: WidgetControlProps;
}

/**
 * The auto-wired relational form control: read-only values compose `RecordReference`;
 * editable values render a searchable `RelationPicker`
 * and — when the related model has a create mutation — offers in-place create
 * with fields derived from its metadata. `FormView` resolves the relation target
 * (model, display field, create) from the SDL and the selected record's label
 * from its own read. Opening the picker starts the bounded option read, and
 * typing searches that collection on the server.
 */
export function RelationFieldWidget(props: RelationFieldWidgetProps): ReactElement {
  if (props.readOnly) return props.value ? <RecordReference
    model={props.relation.resource} id={props.value}
    label={props.selectedOption?.value === props.value ? props.selectedOption.label : undefined}
  /> : <></>;
  return <EditableRelationFieldWidget {...props} />;
}

function EditableRelationFieldWidget(
  {
  value,
  onChange,
  onCommit,
  readOnly,
  relation,
  filters,
  where,
  create,
  searchFields,
  selectedOption,
  placeholder,
  "aria-label": ariaLabel,
  controlRef,
  controlProps,
}: RelationFieldWidgetProps,
): ReactElement {
  const picker = useRelationPickerOptions(relation, {
    value,
    selectedOption,
    filters,
    where,
    searchFields,
  });

  const relatedMetadata = useModelMetadata(relation.resource);
  const createFields = useMemo(
    () => formFieldsFromMetadata(relatedMetadata),
    [relatedMetadata],
  );

  // A "follow" arrow appears only when the related resource has a routed detail page
  // and a record is selected — navigating to it turns the relation into a link.
  const recordHref = useResourceRecordHref(relation.resource);
  const followHref = recordHref && value ? recordHref(value) : undefined;

  function refreshOptions(): void {
    // An explicit create or pencil edit can save before the picker ever opens.
    picker.activate();
    picker.list.refetch();
  }

  return (
    <RelationPicker
      {...controlProps}
      controlRef={controlRef}
      value={value}
      onChange={onChange}
      onCommit={onCommit}
      options={picker.options}
      readOnly={readOnly}
      placeholder={placeholder}
      aria-label={ariaLabel}
      followHref={followHref}
      onOpenChange={picker.onOpenChange}
      onSearchChange={picker.onSearchChange}
      searchState={picker.searchState}
      create={
        create === undefined
          ? relation.canCreate && createFields.length > 0
            ? {
                resource: relation.resource,
                fields: createFields,
                prefillField: relation.labelField,
              }
            : undefined
          : create ?? undefined
      }
      onCreated={refreshOptions}
      // Edit is offered whenever the resource has editable fields — intentionally
      // UX-only, not gated on a `canEdit` flag (resource metadata exposes no
      // per-relation edit capability). The server is the authorization boundary: a denied
      // patch surfaces in the dialog's own error banner.
      edit={
        createFields.length > 0
          ? { resource: relation.resource, fields: createFields }
          : undefined
      }
      onEdited={refreshOptions}
    />
  );
}
