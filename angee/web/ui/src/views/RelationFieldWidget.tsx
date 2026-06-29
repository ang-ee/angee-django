import { useMemo, type ReactElement } from "react";

import {
  useResourceRoute,
} from "../runtime";
import {
  useModelMetadata,
} from "@angee/resources";

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
  const { list, options } = useRelationOptions(relation);

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
      onEdited={() => list.refetch()}
    />
  );
}
