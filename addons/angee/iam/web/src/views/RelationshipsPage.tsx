import { useCallback, useMemo, type ReactElement } from "react";

import {
  AuthoredRowsList,
  Badge,
  Code,
  type DataToolbarGroupOption,
  type ListColumn,
} from "@angee/base";
import type { DocumentData } from "@angee/sdk";

import {
  IamRelationships,
  type IAMRelationshipsVariables,
} from "../documents";
import {
  relationshipRows,
  type IAMRelationshipRow,
} from "../identity-rows";
import { IAM_LIST_LIMIT } from "../list-config";
import { useIamT } from "../i18n";

type IamRelationshipsResult = DocumentData<typeof IamRelationships>;

export function RelationshipsPage(): ReactElement {
  const t = useIamT();
  const relationshipGroupOptions = useMemo<readonly DataToolbarGroupOption[]>(
    () => [
      {
        id: "resourceType",
        label: t("iam.relationships.group.resourceType"),
        group: { field: "resourceType" },
        type: "value",
      },
      {
        id: "subjectType",
        label: t("iam.relationships.group.subjectType"),
        group: { field: "subjectType" },
        type: "value",
      },
      {
        id: "relation",
        label: t("iam.relationships.group.relation"),
        group: { field: "relation" },
        type: "value",
      },
    ],
    [t],
  );
  const relationshipColumns = useMemo<readonly ListColumn<IAMRelationshipRow>[]>(
    () => [
      {
        field: "resourceRef",
        header: t("iam.relationships.column.resourceRef"),
        render: (row) => <Code truncate>{row.resourceRef}</Code>,
      },
      {
        field: "subjectRef",
        header: t("iam.relationships.column.subjectRef"),
        render: (row) => <Code truncate>{row.subjectRef}</Code>,
      },
      { field: "resourceType", header: t("iam.relationships.column.resourceType") },
      { field: "resourceId", header: t("iam.relationships.column.resourceId") },
      {
        field: "relation",
        header: t("iam.relationships.column.relation"),
        render: (row) => <Badge tone="info">{row.relation}</Badge>,
      },
      { field: "subjectType", header: t("iam.relationships.column.subjectType") },
      { field: "subjectId", header: t("iam.relationships.column.subjectId") },
      {
        field: "caveatName",
        header: t("iam.relationships.column.caveat"),
        render: (row) =>
          row.caveatName ? (
            <Badge tone="warning">{row.caveatName}</Badge>
          ) : (
            <span className="text-fg-muted">-</span>
          ),
      },
    ],
    [t],
  );
  const variables = useMemo<IAMRelationshipsVariables>(
    () => ({ pagination: { offset: 0, limit: IAM_LIST_LIMIT } }),
    [],
  );
  const selectRows = useCallback(
    (data: IamRelationshipsResult | undefined) =>
      relationshipRows(data?.relationships.results ?? []),
    [],
  );

  return (
    <AuthoredRowsList
      document={IamRelationships}
      variables={variables}
      selectRows={selectRows}
      columns={relationshipColumns}
      groupOptions={relationshipGroupOptions}
      defaultGroup={{ field: "resourceType" }}
      pageSize={50}
    />
  );
}
