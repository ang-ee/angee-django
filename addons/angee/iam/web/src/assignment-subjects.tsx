import { useAuthoredQuery } from "@angee/refine";
import type { SelectChoice, WidgetField } from "@angee/ui";
import { useMemo } from "react";

import {
  IamAssignmentSubjects,
  type IAMAssignmentSubjectsData,
  type IAMAssignmentSubjectsVariables,
} from "./documents";
import { userDisplayName } from "./identity-labels";
import { useIamT } from "./i18n";
import { IAM_LIST_LIMIT } from "./list-config";

export interface AssignmentSubjectOption extends SelectChoice {
  value: string;
  label: string;
  group: string;
  kind: NonNullable<WidgetField["assignmentSubjectKinds"]>[number];
  id: string;
}

export interface UseAssignmentSubjectsOptions {
  limit?: number;
  /** Native subjects offered by the IAM widget; omitted offers both kinds. */
  kinds?: WidgetField["assignmentSubjectKinds"];
}

export interface AssignmentSubjectsResult {
  options: readonly AssignmentSubjectOption[];
  labels: ReadonlyMap<string, string>;
  isFetching: boolean;
  error: unknown;
  truncated: boolean;
  refetch: () => unknown;
}

export function assignmentSubjectOptions(
  data: IAMAssignmentSubjectsData | undefined,
  labels: { users: string; groups: string },
  kinds?: WidgetField["assignmentSubjectKinds"],
): readonly AssignmentSubjectOption[] {
  const users = (data?.users ?? [])
    .filter((user) => user.is_active)
    .map((user) => ({
      value: user.assignment_subject,
      label: userDisplayName(user, user.id),
      group: labels.users,
      kind: "user" as const,
      id: user.id,
    }));
  const groups = (data?.groups ?? []).map((group) => ({
    value: group.assignment_subject,
    label: group.name,
    group: labels.groups,
    kind: "group" as const,
    id: group.id,
  }));
  return [...users, ...groups].filter((option) => kinds === undefined || kinds.includes(option.kind));
}

export function useAssignmentSubjects(
  { limit = IAM_LIST_LIMIT, kinds }: UseAssignmentSubjectsOptions = {},
): AssignmentSubjectsResult {
  const t = useIamT();
  const variables = useMemo<IAMAssignmentSubjectsVariables>(() => ({ limit }), [limit]);
  const query = useAuthoredQuery(IamAssignmentSubjects, variables);
  const groupLabels = useMemo(() => ({
      users: t("assignmentSubjects.users"),
      groups: t("assignmentSubjects.groups"),
  }), [t]);
  const options = useMemo(() => assignmentSubjectOptions(query.data, groupLabels, kinds), [query.data, groupLabels, kinds]);
  const labels = useMemo(() => new Map(assignmentSubjectOptions(query.data, groupLabels)
    .map((option) => [option.value, option.label])), [query.data, groupLabels]);
  const userCount = query.data?.users_aggregate.aggregate?.count ?? 0;
  const groupCount = query.data?.groups_aggregate.aggregate?.count ?? 0;
  return {
    options,
    labels,
    isFetching: query.isFetching,
    error: query.error,
    truncated: userCount > limit || groupCount > limit,
    refetch: query.refetch,
  };
}
