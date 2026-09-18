import { useMemo, useState, type ReactElement, type Ref } from "react";
import type { CrudFilter } from "@refinedev/core";
import { useDebounce } from "use-debounce";

import {
  useResourceRecordHref,
} from "../../runtime";
import { useModelMetadata } from "@angee/metadata";

import type { RelationOption } from "../../widgets/RelationField";
import {
  formFieldsFromMetadata,
  type RelationFieldInfo,
} from "../resource/model-metadata-defaults";
import { RelationPicker } from "./RelationPicker";
import { useRelationSelectedOption, useRelationOptions } from "./relation-options";

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
  // An option whose label is its own id is a placeholder, not a label: the form
  // builds one for a value it has no record for, which is every preset relation
  // in a create dialog (the draft holds an id and there is no saved record to
  // fold a label from). Treating it as a resolved label is what put `prj_…`,
  // `grp_…` and `stg_…` in front of the user. It still shows while the record
  // read is in flight -- an id beats an empty trigger -- but it no longer stops
  // the read from happening.
  const unresolved = !props.selectedOption || props.selectedOption.label === props.value;
  return props.value && unresolved ? (
    <SelectedRelationFieldWidget {...props} />
  ) : (
    <RelationFieldWidgetBody {...props} />
  );
}

/** A selected relation's label is an independent record read, not an options-page fact. */
function SelectedRelationFieldWidget(
  props: RelationFieldWidgetProps,
): ReactElement {
  const resolved = useRelationSelectedOption(props.relation, props.value);
  return (
    <RelationFieldWidgetBody
      {...props}
      // A resolved option is trusted only when it is a label for THIS value. The
      // read can answer with another record -- stale while refetching, or simply
      // the wrong row -- and an unguarded `resolved` then puts that record's name
      // under this field's id, which is worse than the id it replaced. The
      // id-labelled placeholder holds the trigger until a label for this value
      // arrives.
      selectedOption={
        resolved && resolved.value === props.value ? resolved : props.selectedOption
      }
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
