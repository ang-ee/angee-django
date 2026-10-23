import type { ModelMetadata, ResourceQuery, SchemaFieldMetadata } from "@angee/metadata";
import type { ReactElement } from "react";
import type { FilterClauseField } from "../../toolbars";
import {
  fieldLabel,
  relationFieldInfo,
  relationFieldInfoForQueryField,
  type RelationFieldInfo,
} from "../resource/model-metadata-defaults";
import { RelationPicker } from "./RelationPicker";
import { useRelationPickerOptions } from "./relation-options";

type RelationFilterValueProps = Parameters<NonNullable<FilterClauseField["renderValue"]>>[0]
  & { relation: RelationFieldInfo };

function RelationFilterValue({
  relation,
  onValueChange,
  ...props
}: RelationFilterValueProps): ReactElement {
  const picker = useRelationPickerOptions(relation, { value: props.value });
  return (
    <RelationPicker
      {...props}
      onChange={onValueChange}
      options={picker.options}
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
): readonly FilterClauseField[] {
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
      renderValue: (props) => <RelationFilterValue {...props} relation={relation} />,
    }];
  });
}
