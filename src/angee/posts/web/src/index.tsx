import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Rss } from "lucide-react";
import { connectIntegrationAction } from "@angee/integrate";

import { enPostsMessages } from "./i18n";
import { feedForm } from "./FeedForm";

const posts = defineBaseAddon({
  id: "posts",
  routes: resourcePageRoutes(
    "posts.feeds",
    "/posts/feeds",
    lazyRouteComponent(() => import("./FeedsPage"), "FeedsPage"),
    "posts.Feed",
  ),
  menus: {
    "posts": { label: "Posts", icon: "posts", route: "posts.feeds" },
  },
  icons: { posts: Rss },
  i18n: { posts: enPostsMessages },
  forms: { "posts.Feed": feedForm },
  containers: {
    "posts.Feed#actions": {
      "posts.connect": connectIntegrationAction(),
    },
  },
});

export default posts;
