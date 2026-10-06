import {
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddonRoute,
} from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Tag as TagIcon, Tags as TagsIcon } from "lucide-react";

import { enTagsMessages } from "./i18n";
import { tagsWidget } from "./TagsField";

const TAGS_ID = "tags";

/** The field widget key the backend's `TaggedNode.tags` names in resource metadata. */
export const TAGS_WIDGET = "angee.tags.tags";

const tagsRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes(
    "tags.tags",
    "/tags",
    lazyRouteComponent(() => import("./views/TagsPage"), "TagsPage"),
    "tags.Tag",
    { detailName: "tags.tag", menu: "tags.tags" },
  ),
];

const tagsMenu: readonly BaseMenuItem[] = [
  {
    id: TAGS_ID,
    label: "Tags",
    icon: "tags-group",
    group: "platform",
    children: [
      {
        id: "tags.tags",
        label: "Tags",
        icon: "tags-tag",
        route: "tags.tags",
      },
    ],
  },
];

/**
 * The `@angee/tags` rendered addon: the tag vocabulary page and the `tags`
 * field widget ({@link tagsWidget}). Tags appear only where an owner places the
 * field — a record form's `<Field name="tags" />` or a list column — on a model
 * whose addon composes the backend's `TaggedNode`; this addon names no other
 * addon's model.
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
  widgets: { [TAGS_WIDGET]: tagsWidget },
  containers: {
    // Scope-specific tag addons add facets, columns and form fields to the Tags page.
    "tags.tags#facets": {},
    "tags.tags#columns": {},
    "tags.tags#fields": {},
  },
});

export default tags;
