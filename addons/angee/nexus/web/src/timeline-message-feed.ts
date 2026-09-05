import { useAuth } from "@angee/app";
import { messageFeedOptions } from "@angee/messaging";
import {
  authoredQueryKey,
  requestAuthoredData,
  sharedAuthoredMeta,
  useActiveDataProviderName,
  useAuthoredErrorPolicy,
  useAuthoredLiveInterest,
  type DocumentData,
} from "@angee/refine";
import { useDataProvider } from "@refinedev/core";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";

import { NexusTimeline, NexusTimelineRevalidate } from "./documents";

// Handle/Party supply sender identity and membership; Thread supplies its title.
// Circle moves affect subtree membership without writing CircleMember. The
// computed PartyHandle projection has no change root; Handle owns its writes.
const TIMELINE_MODELS = [
  "messaging.Message", "messaging.Thread", "parties.Handle", "parties.Party",
] as const;
const CIRCLE_TIMELINE_MODELS = [...TIMELINE_MODELS, "parties.Circle", "parties.CircleMember"] as const;

/** Nexus owns party/circle membership and its lighter message projection. */
export function useTimelineMessageFeed(scopeId: string, circle: boolean) {
  const client = useQueryClient();
  const dataProvider = useDataProvider();
  const provider = useActiveDataProviderName() ?? "default";
  const actor = useAuth().user?.id;
  const enabled = Boolean(actor && scopeId);
  const models = circle ? CIRCLE_TIMELINE_MODELS : TIMELINE_MODELS;
  const scope = { partyId: scopeId, circleId: scopeId, circle, search: "" };
  const queryKey = ["angee", "authored", "message-feed", actor,
    authoredQueryKey(NexusTimeline, { ...scope, limit: 30 }, provider)] as const;
  const configured = messageFeedOptions(client, {
    queryKey,
    pageSize: 30,
    async window(beforeCursor, throughCursor, limit, context) {
      const data = await requestAuthoredData<DocumentData<typeof NexusTimeline>>(
        dataProvider, provider, NexusTimeline, { ...scope, beforeCursor, throughCursor, limit }, context,
      );
      const page = circle ? data.circle_message_feed : data.party_message_feed;
      if (!page) throw new Error("Timeline window returned no scope data.");
      return page;
    },
    async revalidate(ids, context) {
      const data = await requestAuthoredData<DocumentData<typeof NexusTimelineRevalidate>>(
        dataProvider, provider, NexusTimelineRevalidate, { ...scope, ids }, context,
      );
      const page = circle ? data.circle_message_feed_revalidate : data.party_message_feed_revalidate;
      if (!page) throw new Error("Timeline revalidation returned no scope data.");
      return page;
    },
  });
  useAuthoredLiveInterest(enabled, models);
  useAuthoredErrorPolicy([queryKey]);
  return useInfiniteQuery({ ...configured, enabled, meta: sharedAuthoredMeta(client, queryKey, models) });
}
