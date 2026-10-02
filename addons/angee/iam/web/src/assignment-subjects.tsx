import { useAuthoredQuery } from "@angee/refine";
import type { SelectChoice } from "@angee/ui";
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
  kind: "user" | "group";
  id: string;
}

export interface UseAssignmentSubjectsOptions {
  limit?: number;
  /** A FormSpec item relation narrows the offered subjects to its resource. */
  resource?: string;
}

export interface AssignmentSubjectsResult {
  options: readonly AssignmentSubjectOption[];
  isFetching: boolean;
  error: unknown;
  truncated: boolean;
  refetch: () => unknown;
}

export function assignmentSubjectOptions(
  data: IAMAssignmentSubjectsData | undefined,
  labels: { users: string; groups: string },
  resource?: string,
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
  if (resource === "iam.User") return users;
  if (resource === "iam.Group") return groups;
  return resource ? [] : [...users, ...groups];
}

export function useAssignmentSubjects(
  { limit = IAM_LIST_LIMIT, resource }: UseAssignmentSubjectsOptions = {},
): AssignmentSubjectsResult {
  const t = useIamT();
  const variables = useMemo<IAMAssignmentSubjectsVariables>(() => ({ limit }), [limit]);
  const query = useAuthoredQuery(IamAssignmentSubjects, variables);
  const options = useMemo(
    () => assignmentSubjectOptions(query.data, {
      users: t("assignmentSubjects.users"),
      groups: t("assignmentSubjects.groups"),
    }, resource),
    [query.data, resource, t],
  );
  const userCount = query.data?.users_aggregate.aggregate?.count ?? 0;
  const groupCount = query.data?.groups_aggregate.aggregate?.count ?? 0;
  return {
    options,
    isFetching: query.isFetching,
    error: query.error,
    truncated: userCount > limit || groupCount > limit,
    refetch: query.refetch,
  };
}
