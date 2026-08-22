import type { ReactionGroupCopy } from "@angee/ui";

import type { PostsT } from "./i18n";

/** Namespace adapter for the shared reaction-group projection owner. */
export function postsReactionCopy(t: PostsT): ReactionGroupCopy {
  return {
    count: (values) => t("post.reactionCount", values),
    named: (values) => t("post.reactionTitle", values),
  };
}
