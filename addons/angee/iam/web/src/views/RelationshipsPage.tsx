import { useMemo, type ReactElement } from "react";

import {
  Badge,
  Code,
  ListView,
  type ListColumn,
  type ResourceToolbarGroupOption,
} from "@angee/ui";

import { useIamT } from "../i18n";

// The `iam.Relationship` Hasura resource (`hasura_model_resource` over the active
// REBAC relationship store, `addons/angee/iam/schema.py`, marked rowModel
// "client"): the group axes are denormalized display strings on the node, not
// RelationshipRegistry columns, so — like the original authored page — it fetches
// the bounded admin tuple set once and filters/sorts/groups in the browser. The
// `resource`/`subject` refs compose `<type>:<id>` in the cell.
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

function relationshipGroupOptions(
  t: (key: string) => string,
): readonly ResourceToolbarGroupOption[] {
  return [
    {
      id: "resource_type",
      label: t("iam.relationships.column.resourceType"),
      group: { field: "resource_type" },
      type: "value",
    },
    {
      id: "subject_type",
      label: t("iam.relationships.column.subjectType"),
      group: { field: "subject_type" },
      type: "value",
    },
    {
      id: "relation",
      label: t("iam.relationships.column.relation"),
      group: { field: "relation" },
      type: "value",
    },
  ];
}

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
      groupOptions={relationshipGroupOptions(t)}
      defaultGroup={{ field: "resource_type" }}
      pageSize={50}
    />
  );
}
