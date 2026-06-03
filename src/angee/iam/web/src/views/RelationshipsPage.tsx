import { useMemo, type ReactElement } from "react";

import {
  Badge,
  Code,
  RowsListView,
  type DataToolbarGroupOption,
  type ListColumn,
} from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import {
  IAM_RELATIONSHIPS_QUERY,
  type IAMRelationshipsData,
  type IAMRelationshipsVariables,
} from "../documents";
import {
  relationshipRows,
  type IAMRelationshipRow,
} from "../identity-rows";

const RELATIONSHIP_LIMIT = 500;

const relationshipGroupOptions: readonly DataToolbarGroupOption[] =
  [
    {
      id: "resourceType",
      label: "Resource Type",
      group: { field: "resourceType" },
      type: "value",
    },
    {
      id: "subjectType",
      label: "Subject Type",
      group: { field: "subjectType" },
      type: "value",
    },
    {
      id: "relation",
      label: "Relation",
      group: { field: "relation" },
      type: "value",
    },
  ];

const relationshipColumns: readonly ListColumn<IAMRelationshipRow>[] = [
  {
    field: "resourceRef",
    header: "Resource Ref",
    render: (row) => <Code truncate>{row.resourceRef}</Code>,
  },
  {
    field: "subjectRef",
    header: "Subject Ref",
    render: (row) => <Code truncate>{row.subjectRef}</Code>,
  },
  { field: "resourceType", header: "Resource Type" },
  { field: "resourceId", header: "Resource ID" },
  {
    field: "relation",
    header: "Relation",
    render: (row) => <Badge variant="info">{row.relation}</Badge>,
  },
  { field: "subjectType", header: "Subject Type" },
  { field: "subjectId", header: "Subject ID" },
  {
    field: "caveatName",
    header: "Caveat",
    render: (row) =>
      row.caveatName ? (
        <Badge variant="warning">{row.caveatName}</Badge>
      ) : (
        <span className="text-fg-muted">-</span>
      ),
  },
];

export function RelationshipsPage(): ReactElement {
  const variables = useMemo<IAMRelationshipsVariables>(
    () => ({ pagination: { offset: 0, limit: RELATIONSHIP_LIMIT } }),
    [],
  );
  const query = useAuthoredQuery<
    IAMRelationshipsData,
    IAMRelationshipsVariables
  >(IAM_RELATIONSHIPS_QUERY, variables);
  const rows = useMemo(
    () => relationshipRows(query.data?.relationships.results ?? []),
    [query.data],
  );

  return (
    <RowsListView
      rows={rows}
      columns={relationshipColumns}
      fetching={query.fetching}
      error={query.error}
      groupOptions={relationshipGroupOptions}
      defaultGroup={{ field: "resourceType" }}
      pageSize={50}
    />
  );
}
