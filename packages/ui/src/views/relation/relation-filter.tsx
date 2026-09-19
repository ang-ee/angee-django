import type { ModelMetadata, ResourceQuery, SchemaFieldMetadata } from "@angee/metadata";
import type { ReactElement } from "react";
import type { ResourceToolbarFilterField } from "../../toolbars";
import {
  fieldLabel,
  relationFieldInfo,
  relationFieldInfoForQueryField,
  type RelationFieldInfo,
} from "../resource/model-metadata-defaults";
import { RelationPicker } from "./RelationPicker";
import { useRelationPickerOptions } from "./relation-options";

interface RelationFilterValueProps {
  relation: RelationFieldInfo;
  value: string;
  onValueChange: (value: string) => void;
  label: string;
}

function RelationFilterValue({
  relation,
  value,
  onValueChange,
  label,
}: RelationFilterValueProps): ReactElement {
  const picker = useRelationPickerOptions(relation, { value });
  return (
    <RelationPicker
      value={value}
      onChange={onValueChange}
      options={picker.options}
      aria-label={label}
      onOpenChange={picker.onOpenChange}
      onSearchChange={picker.onSearchChange}
      searchState={picker.searchState}
    />
  );
}

/** Add the existing lazy relation picker to complete query-derived fields. */
export function relationFilterFields(
  query: ResourceQuery,
  modelMetadata: ModelMetadata | null,
  schemaMetadata: SchemaFieldMetadata,
): readonly ResourceToolbarFilterField[] {
  return Object.entries(query.fields).flatMap(([fieldName, capability]) => {
    if (!capability.filter?.operators.length) return [];
    const relation = modelMetadata
      ? relationFieldInfo(fieldName, modelMetadata, schemaMetadata)
      : relationFieldInfoForQueryField(capability, schemaMetadata);
    if (!relation) return [];
    const label = String(fieldLabel(fieldName, modelMetadata?.fields[fieldName]));
    return [{
      id: fieldName,
      field: fieldName,
      label,
      renderValue: ({ value, onValueChange }) => (
        <RelationFilterValue
          relation={relation}
          value={value}
          onValueChange={onValueChange}
          label={label}
        />
      ),
    }];
  });
}
