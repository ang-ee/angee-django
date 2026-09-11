import { useMemo, useState, type ReactElement, type Ref } from "react";
import { useOne, type CrudFilter, type HttpError } from "@refinedev/core";
import { refineFieldsFromPaths } from "@angee/refine";
import { useDebounce } from "use-debounce";

import {
  useResourceRecordHref,
} from "../../runtime";
import { useModelMetadata, refineResourceName } from "@angee/metadata";

import type { RelationOption } from "../../widgets/RelationField";
import {
  formFieldsFromMetadata,
  type RelationFieldInfo,
} from "../resource/model-metadata-defaults";
import { RelationPicker } from "./RelationPicker";
import { relationSelectedOption, useRelationOptions } from "./relation-options";

export interface RelationFieldWidgetProps {
  value?: string | null;
  onChange?: (value: string) => void;
  onCommit?: () => void;
  readOnly?: boolean;
  relation: RelationFieldInfo;
  /** Server-side filters narrowing the rows offered by this relation picker. */
  filters?: readonly CrudFilter[];
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
}

/**
 * The auto-wired relational form control: renders a searchable `RelationPicker`
 * and — when the related model has a create mutation — offers in-place create
 * with fields derived from its metadata. `FormView` resolves the relation target
 * (model, display field, create) from the SDL and the selected record's label
 * from its own read. Opening the picker starts the bounded option read, and
 * typing searches that collection on the server.
 */
export function RelationFieldWidget(
  props: RelationFieldWidgetProps,
): ReactElement {
  return props.value && !props.selectedOption ? (
    <SelectedRelationFieldWidget {...props} />
  ) : (
    <RelationFieldWidgetBody {...props} />
  );
}

/** A selected relation's label is an independent record read, not an options-page fact. */
function SelectedRelationFieldWidget(
  props: RelationFieldWidgetProps,
): ReactElement {
  const metadata = useModelMetadata(props.relation.resource);
  const resource = metadata?.resource;
  const fields = useMemo(
    () => refineFieldsFromPaths(["id", props.relation.labelField]),
    [props.relation.labelField],
  );
  const read = useOne<Record<string, unknown> & { id: string }, HttpError>({
    resource: resource ? refineResourceName(resource) : "__angee_disabled__",
    dataProviderName: resource?.schemaName,
    id: props.value ?? "",
    meta: { fields },
    queryOptions: { enabled: Boolean(resource && props.value) },
  });
  return (
    <RelationFieldWidgetBody
      {...props}
      selectedOption={relationSelectedOption(
        read.result,
        props.relation.labelField,
      )}
    />
  );
}

function RelationFieldWidgetBody({
  value,
  onChange,
  onCommit,
  readOnly,
  relation,
  filters,
  searchFields,
  selectedOption,
  placeholder,
  "aria-label": ariaLabel,
  controlRef,
}: RelationFieldWidgetProps): ReactElement {
  // Latch the first popover-open so the option query fires once and stays
  // enabled (so a later relabel/refetch keeps working), but never on a
  // read-only/show render where the popover never opens.
  const [opened, setOpened] = useState(false);
  const [search, setSearch] = useState("");
  const [searchText] = useDebounce(search, 250);
  const { list, options: fetched } = useRelationOptions(relation, {
    enabled: opened,
    filters,
    searchText,
    searchFields,
  });
  // The selected record's own (folded) label shows immediately; once the list
  // loads, its fresh label for the same record wins, and the selected option is
  // kept available even for a record beyond the fetched window.
  const options = useMemo(
    () =>
      selectedOption &&
      !fetched.some((option) => option.value === selectedOption.value)
        ? [selectedOption, ...fetched]
        : fetched,
    [fetched, selectedOption],
  );

  const relatedMetadata = useModelMetadata(relation.resource);
  const createFields = useMemo(
    () => formFieldsFromMetadata(relatedMetadata),
    [relatedMetadata],
  );

  // A "follow" arrow appears only when the related resource has a routed detail page
  // and a record is selected — navigating to it turns the relation into a link.
  const recordHref = useResourceRecordHref(relation.resource);
  const followHref = recordHref && value ? recordHref(value) : undefined;

  return (
    <RelationPicker
      controlRef={controlRef}
      value={value}
      onChange={onChange}
      onCommit={onCommit}
      options={options}
      readOnly={readOnly}
      placeholder={placeholder}
      aria-label={ariaLabel}
      followHref={followHref}
      onOpenChange={(open) => {
        setSearch("");
        if (open) setOpened(true);
      }}
      onSearchChange={setSearch}
      searchState={{
        pending: list.fetching || search !== searchText,
        error: list.error,
        retry: list.refetch,
      }}
      create={
        relation.canCreate && createFields.length > 0
          ? {
              resource: relation.resource,
              fields: createFields,
              prefillField: relation.labelField,
            }
          : undefined
      }
      onCreated={() => list.refetch()}
      // Edit is offered whenever the resource has editable fields — intentionally
      // UX-only, not gated on a `canEdit` flag (resource metadata exposes no
      // per-relation edit capability). The server is the authorization boundary: a denied
      // patch surfaces in the dialog's own error banner.
      edit={
        createFields.length > 0
          ? { resource: relation.resource, fields: createFields }
          : undefined
      }
      onEdited={() => {
        // A pencil-edit relabel can happen without the dropdown ever opening;
        // enable the option query so the refetch carries the fresh label.
        setOpened(true);
        list.refetch();
      }}
    />
  );
}
