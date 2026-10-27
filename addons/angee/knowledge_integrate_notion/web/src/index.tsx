import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";

import { NOTION_CONNECTION_MODEL, notionConnectionForm } from "./NotionConnectionForm";
import { enNotionMessages } from "./i18n";

export default defineBaseAddon({
  id: "knowledge_integrate_notion",
  i18n: { knowledge_integrate_notion: enNotionMessages },
  routes: resourcePageRoutes(
    "knowledge_integrate_notion.connections",
    "/integrate/notion",
    lazyRouteComponent(() => import("./NotionConnectionsPage"), "NotionConnectionsPage"),
    NOTION_CONNECTION_MODEL,
  ),
  menus: [{
    id: "knowledge_integrate_notion.connections",
    label: "Notion",
    icon: "integration",
    parentId: "integrate",
    route: "knowledge_integrate_notion.connections",
  }],
  forms: { [NOTION_CONNECTION_MODEL]: notionConnectionForm },
});
