import { useMemo, useState, type ReactElement } from "react";

import {
  useResourceRoute,
} from "../runtime";
import {
  useModelMetadata,
} from "@angee/metadata";

import type { RelationOption } from "../widgets/RelationField";
import { recordPath } from "./resource-routing";
import {
  formFieldsFromMetadata,
  type RelationFieldInfo,
} from "./model-metadata-defaults";
import { RelationPicker } from "./RelationPicker";
import { useRelationOptions } from "./relation-options";

export interface RelationFieldWidgetProps {
  value?: string | null;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  relation: RelationFieldInfo;
  /**
   * The already-loaded selected record as a picker option (id + folded label),
   * derived by `FormView` from the parent read. Shows the trigger label before
   * — and without — the option list ever loading, so a read-only/show view
   * never fires the option query; the freshly fetched label wins once loaded.
   */
  selectedOption?: RelationOption;
  placeholder?: string;
  "aria-label"?: string;
}

/**
 * The auto-wired relational form control: renders a searchable `RelationPicker`
 * and — when the related model has a create mutation — offers in-place create
 * with fields derived from its metadata. `FormView` resolves the relation target
 * (model, display field, create) from the SDL and the selected record's label
 * from its own read, so the 200-row option list is fetched only once the picker
 * is first opened (a read-only/show view never opens it, so never fetches it).
 */
export function RelationFieldWidget({
  value,
  onChange,
  readOnly,
  relation,
  selectedOption,
  placeholder,
  "aria-label": ariaLabel,
}: RelationFieldWidgetProps): ReactElement {
  // Latch the first popover-open so the option query fires once and stays
  // enabled (so a later relabel/refetch keeps working), but never on a
  // read-only/show render where the popover never opens.
  const [opened, setOpened] = useState(false);
  const { list, options: fetched } = useRelationOptions(relation, {
    enabled: opened,
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
  const basePath = useResourceRoute(relation.resource);
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
      onOpenChange={(open) => {
        if (open) setOpened(true);
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
