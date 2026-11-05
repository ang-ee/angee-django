import { useAccessPeople, type AccessRoleOwnerProps, type AccessPerson } from "@angee/iam";
import * as React from "react";

import { useCarriesChatter } from "./FollowRecordChrome";
import { useRecordThreadQuery } from "./useRecordThreadQuery";

/** Annotate IAM's existing reader roster with Messaging's readable follower facts. */
export function AccessFollowers({ targetId, resource }: AccessRoleOwnerProps): null {
  const carries = useCarriesChatter(resource.modelLabel);
  const query = useRecordThreadQuery({
    modelLabel: resource.modelLabel, recordId: targetId,
  }, carries);
  const people = React.useMemo<readonly AccessPerson[]>(() =>
    (query.data?.record_thread?.followers ?? []).flatMap(({ user }) => user
      ? [{ subject: user.assignment_subject, label: user.username ?? user.assignment_subject, following: true }]
      : []), [query.data]);
  const onRetry = React.useCallback(() => { void query.refetch(); }, [query.refetch]);
  const state = React.useMemo(() => ({ people, fetching: query.isFetching, error: query.error, onRetry }),
    [people, query.isFetching, query.error, onRetry]);
  useAccessPeople("messaging.followers", state);
  return null;
}
