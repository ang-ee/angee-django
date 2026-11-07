import { useAccessPeople, type AccessRoleOwnerProps, type AccessPerson } from "@angee/iam";
import { useAuthoredQuery } from "@angee/refine";
import * as React from "react";

import { MessagingAccessFollowersDocument, READ_MODELS } from "./documents";
import { useCarriesChatter } from "./FollowRecordChrome";

/** Annotate IAM's existing reader roster with Messaging's readable follower facts. */
export function AccessFollowers({ targetId, resource }: AccessRoleOwnerProps): null {
  const carries = useCarriesChatter(resource.modelLabel);
  const query = useAuthoredQuery(MessagingAccessFollowersDocument, {
    modelLabel: resource.modelLabel, recordId: targetId,
  }, {
    enabled: carries && Boolean(targetId), models: [...READ_MODELS, resource.modelLabel],
    records: [{ model: resource.modelLabel, id: targetId }],
  });
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
