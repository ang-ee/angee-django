import { useAuthoredMutation } from "@angee/refine";
import { useModelMetadata } from "@angee/metadata";
import { Button, Glyph, errorMessage, useRecordChromeContext, useToast } from "@angee/ui";
import * as React from "react";

import { READ_MODELS, SetRecordFollowingDocument, THREAD_CHATTER_EDGE } from "./documents";
import { useMessagingT } from "./i18n";
import { useRecordThreadQuery } from "./useRecordThreadQuery";

/** Whether rows of `modelLabel` carry a chatter thread at all. */
export function useCarriesChatter(modelLabel: string): boolean {
  const listed = useModelMetadata(modelLabel);
  const canonical = useModelMetadata(listed?.resource.canonicalLabel ?? modelLabel);
  return Boolean((listed?.resource.recordEdges ?? canonical?.resource.recordEdges)?.includes(THREAD_CHATTER_EDGE));
}

/**
 * The record-header Follow control (`form#chrome`): one icon toggle backed by the
 * record thread's `is_following`. Absent on types without a thread and on records
 * whose thread cannot be read; the Discussion's overflow holds what to be told about.
 */
export function FollowRecordChrome(): React.ReactElement | null {
  const record = useRecordChromeContext();
  const carries = useCarriesChatter(record.resource);
  if (!carries || !record.recordId) return null;
  return <FollowToggle modelLabel={record.resource} recordId={record.recordId} />;
}

function FollowToggle({ modelLabel, recordId }: { modelLabel: string; recordId: string }): React.ReactElement | null {
  const t = useMessagingT();
  const toast = useToast();
  const variables = React.useMemo(() => ({ modelLabel, recordId, messageLimit: 1 }), [modelLabel, recordId]);
  const query = useRecordThreadQuery(variables);
  const [setFollowing, state] = useAuthoredMutation(SetRecordFollowingDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.set_record_following,
  });
  const payload = query.data?.record_thread;
  if (!payload || payload.error_code) return null;
  const following = Boolean(payload.is_following);
  const label = following ? t("chatter.following") : t("chatter.follow");
  const defaults = (payload.subtypes ?? []).filter((subtype) => !subtype.internal && subtype.default).map((subtype) => subtype.key);
  return (
    <Button
      type="button"
      size="iconSm"
      variant="ghost"
      aria-label={label}
      title={label}
      pressed={following}
      disabled={state.fetching}
      onClick={() => {
        void setFollowing({ modelLabel, recordId, following: !following, notificationPolicy: "inbox", subtypeKeys: following ? [] : defaults })
          .catch((cause: unknown) => toast({ tone: "danger", title: errorMessage(cause, t("error.following")) }));
      }}
    >
      <Glyph name={following ? "bell-off" : "bell"} decorative />
    </Button>
  );
}
