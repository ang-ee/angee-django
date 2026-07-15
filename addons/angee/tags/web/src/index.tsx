import {
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddonRoute,
} from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Tag as TagIcon, Tags as TagsIcon } from "lucide-react";

import { enTagsMessages } from "./i18n";
import { RecordTagsPane } from "./RecordTagsPane";

export {
  TAG_SCOPE_COLUMN_SLOT,
  TAG_SCOPE_FACET_SLOT,
  TAG_SCOPE_FIELD_SLOT,
} from "./slots";

const TAGS_ID = "tags";

const tagsRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes(
    "tags.list",
    "/tags",
    lazyRouteComponent(() => import("./views/TagsPage"), "TagsPage"),
    "tags.Tag",
    { detailName: "tags.detail", menu: "tags.list" },
  ),
];

const tagsMenu: readonly BaseMenuItem[] = [
  {
    id: TAGS_ID,
    label: "Tags",
    icon: "tags-group",
    sidebar: true,
    children: [
      {
        id: "tags.list",
        label: "Vocabulary",
        icon: "tags-tag",
        route: "tags.list",
      },
    ],
  },
];

/**
 * The `@angee/tags` rendered addon. It contributes the tag vocabulary page and a
 * record-scoped **Tags** chatter tab ({@link RecordTagsPane}) — the polymorphic
 * tag widget renders in every record's console aside (the party detail included)
 * without any change to the addon that owns the record, which is the whole point
 * of the polymorphic edge. The party-list tag facet lands when `angee.tags` is
 * promoted and `parties` composes it (a facet is only declarable on the list its
 * owning addon renders); until then the vocabulary page and the record pane cover
 * curation and assignment.
 */
const tags = defineBaseAddon({
  id: TAGS_ID,
  routes: tagsRoutes,
  menus: tagsMenu,
  i18n: { tags: enTagsMessages },
  icons: {
    "tags-group": TagsIcon,
    "tags-tag": TagIcon,
    tag: TagIcon,
  },
  chatter: [
    {
      id: "tags",
      sequence: 15,
      label: "Tags",
      icon: "tag",
      render: (context) => <RecordTagsPane context={context} />,
    },
  ],
});

export default tags;
