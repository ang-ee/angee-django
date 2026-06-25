import { useMemo, type ReactElement } from "react";

import {
  Badge,
  Code,
  ListView,
  type ListColumn,
} from "@angee/base";

import { useIamT } from "../i18n";

// The `iam.Relationship` Hasura resource (`hasura_model_resource` over the active
// REBAC relationship store, `addons/angee/iam/schema.py`): a real queryset, so a
// server row model. The denormalized type strings live behind FKs in registry
// storage, so the resource exposes no groupable axis — the page lists, filters
// (relation/caveat), and sorts server-side without grouping. The `resource`/
// `subject` refs compose `<type>:<id>` in the cell.
interface RelationshipResourceRow extends Record<string, unknown> {
  id: string;
  resource_type: string;
  resource_id: string;
  relation: string;
  subject_type: string;
  subject_id: string;
  caveat_name: string;
}

const ref = (type: string, id: string): string => `${type}:${id}`;

export function RelationshipsPage(): ReactElement {
  const t = useIamT();
  const relationshipColumns = useMemo<readonly ListColumn<RelationshipResourceRow>[]>(
    () => [
      {
        field: "resource_id",
        header: t("iam.relationships.column.resourceRef"),
        render: (row) => <Code truncate>{ref(row.resource_type, row.resource_id)}</Code>,
      },
      {
        field: "subject_id",
        header: t("iam.relationships.column.subjectRef"),
        render: (row) => <Code truncate>{ref(row.subject_type, row.subject_id)}</Code>,
      },
      { field: "resource_type", header: t("iam.relationships.column.resourceType") },
      { field: "resource_id", header: t("iam.relationships.column.resourceId") },
      {
        field: "relation",
        header: t("iam.relationships.column.relation"),
        render: (row) => <Badge tone="info">{row.relation}</Badge>,
      },
      { field: "subject_type", header: t("iam.relationships.column.subjectType") },
      { field: "subject_id", header: t("iam.relationships.column.subjectId") },
      {
        field: "caveat_name",
        header: t("iam.relationships.column.caveat"),
        render: (row) =>
          row.caveat_name ? (
            <Badge tone="warning">{row.caveat_name}</Badge>
          ) : (
            <span className="text-fg-muted">-</span>
          ),
      },
    ],
    [t],
  );

  return (
    <ListView<RelationshipResourceRow>
      resource="iam.Relationship"
      columns={relationshipColumns}
      pageSize={50}
    />
  );
}
