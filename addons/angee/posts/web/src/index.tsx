import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Rss } from "lucide-react";

import { enPostsMessages } from "./i18n";

const posts = defineBaseAddon({
  id: "posts",
  routes: resourcePageRoutes(
    "posts.feeds",
    "/posts/feeds",
    lazyRouteComponent(() => import("./FeedsPage"), "FeedsPage"),
    "posts.Feed",
  ),
  menus: [
    {
      id: "posts",
      label: "Posts",
      icon: "posts",
      route: "posts.feeds",
    },
  ],
  icons: { posts: Rss },
  i18n: { posts: enPostsMessages },
});

export default posts;
