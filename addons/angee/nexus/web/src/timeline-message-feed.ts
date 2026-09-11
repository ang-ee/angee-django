import { useAuth } from "@angee/app";
import { messageFeedWindow, messageFeedRevalidation } from "@angee/messaging";
import { useAuthoredKeysetFeed } from "@angee/refine";
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
  const scope = { partyId: scopeId, circleId: scopeId, circle, search: "" };
  return useAuthoredKeysetFeed({
    actor: useAuth().user?.id,
    enabled: Boolean(scopeId),
    models: circle ? CIRCLE_TIMELINE_MODELS : TIMELINE_MODELS,
    pageSize: 30,
    window: {
      document: NexusTimeline,
      variables: (beforeCursor, throughCursor, limit) => ({ ...scope, beforeCursor, throughCursor, limit }),
      select: (data) => {
        const page = circle ? data.circle_message_feed : data.party_message_feed;
        if (!page) throw new Error("Timeline window returned no scope data.");
        return messageFeedWindow(page);
      },
    },
    revalidate: {
      document: NexusTimelineRevalidate,
      variables: (ids) => ({ ...scope, ids }),
      select: (data) => {
        const page = circle ? data.circle_message_feed_revalidate : data.party_message_feed_revalidate;
        if (!page) throw new Error("Timeline revalidation returned no scope data.");
        return messageFeedRevalidation(page);
      },
    },
  });
}
