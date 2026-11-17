import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute, type MenuDeclarations } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { FolderTree as TagCategoryIcon, Tag as TagIcon, Tags as TagsIcon } from "lucide-react";

import { enTagsMessages } from "./i18n";
import { tagForm } from "./views/TagForm";
import { recordTagsWidget } from "./RecordTagsWidget";

const TAGS_ID = "tags";

const tagsRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes(
    "tags.tags",
    "/tags",
    lazyRouteComponent(() => import("./views/TagsPage"), "TagsPage"),
    "tags.Tag",
    { detailName: "tags.tag", menu: "tags.tags" },
  ),
  ...resourcePageRoutes(
    "tags.categories",
    "/tags/categories",
    lazyRouteComponent(() => import("./views/TagCategoriesPage"), "TagCategoriesPage"),
    "tags.TagCategory",
    { detailName: "tags.category", menu: "tags.categories" },
  ),
];

const tagsMenu: MenuDeclarations = {
  [TAGS_ID]: { label: "Tags", icon: "tags-group", group: "platform" },
  "tags.tags": { label: "Tags", icon: "tags-tag", route: "tags.tags", parent: TAGS_ID },
  "tags.categories": { label: "Categories", icon: "tags-category", route: "tags.categories", parent: TAGS_ID },
};

/**
 * The `@angee/tags` rendered addon: the tag vocabulary page, the tag categories
 * page and the tag form (shared with a picker's "Create and edit…"). Tags appear
 * only where an owner places the field — a record form's `<Field name="tags" />`
 * or a list column — on a model whose addon composes the backend's `TaggedNode`;
 * the form edits it as its standard to-many chips field and saves it with the
 * record. The `angee.tags.recordTags` widget uses independent tag verbs for
 * records with read-only scalar fields and shows one picker per tag category.
 * This addon names no other addon's model.
 */
const tags = defineBaseAddon({
  id: TAGS_ID,
  routes: tagsRoutes,
  menus: tagsMenu,
  i18n: { tags: enTagsMessages },
  icons: {
    "tags-group": TagsIcon,
    "tags-tag": TagIcon,
    "tags-category": TagCategoryIcon,
    tag: TagIcon,
  },
  forms: { "tags.Tag": tagForm },
  widgets: { "angee.tags.recordTags": recordTagsWidget },
  containers: {
    // Scope-specific tag addons add facets, columns and form fields to the Tags page.
    "tags.tags#facets": {},
    "tags.tags#columns": {},
    "tags.tags#fields": {},
  },
});

export default tags;
