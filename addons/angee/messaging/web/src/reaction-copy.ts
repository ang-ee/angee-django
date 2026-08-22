import type { ReactionGroupCopy } from "@angee/ui";

import type { MessagingT } from "./i18n";

/** Namespace adapter for the shared reaction-group projection owner. */
export function messagingReactionCopy(t: MessagingT): ReactionGroupCopy {
  return {
    count: (values) => t("message.reactionCount", values),
    named: (values) => t("message.reactionTitle", values),
  };
}
