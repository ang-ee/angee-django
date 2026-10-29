import type { BaseAddonRoute } from "@angee/app";
import { defineBaseAddon, resourcePageRoutes, type MenuDeclarations } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { BookOpen, FileStack, FileText, Library } from "lucide-react";

import { enKnowledgeMessages } from "./i18n";
import { pageBacklinksGroup, pageBoundToGroup, recordPagesGroup } from "./RecordPagesPane";
import { vaultCreateForm } from "./views/vault-form";

export { KnowledgePageView, type KnowledgePageViewProps } from "./KnowledgePageView";
export { recordPagesGroup, recordPagesTarget, type RecordPagesGroupOptions, type RecordPagesTarget } from "./RecordPagesPane";
export { RecordNotesStream, recordNotesTab, type RecordNotesStreamProps, type RecordNotesTabOptions } from "./RecordNotesStream";

const KNOWLEDGE_ID = "knowledge";

const knowledgeRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes("knowledge.home", "/knowledge", lazyRouteComponent(() => import("./views/KnowledgePage"), "KnowledgePage"), "knowledge.Page", { detailName: "knowledge.page", menu: KNOWLEDGE_ID }),
  {
    // The vaults admin. A static `/knowledge/settings` outranks the
    // `/knowledge/$id` page route, so it is a sibling, not a page id.
    name: "knowledge.settings",
    path: "/knowledge/settings",
    component: lazyRouteComponent(() => import("./views/KnowledgeSettingsPage"), "KnowledgeSettingsPage"),
  },
];

const knowledgeMenu: MenuDeclarations = {
  [KNOWLEDGE_ID]: { label: "Knowledge", icon: "knowledge", route: "knowledge.home" },
  "knowledge.home": { label: "Wiki", icon: "knowledge", route: "knowledge.home", parent: KNOWLEDGE_ID },
  "knowledge.settings": { label: "Settings", icon: "vault", route: "knowledge.settings", parent: KNOWLEDGE_ID },
};

// Glyphs the wiki reaches for that the base registry doesn't carry.
const knowledge = defineBaseAddon({
  id: KNOWLEDGE_ID,
  routes: knowledgeRoutes,
  menus: knowledgeMenu,
  i18n: { knowledge: enKnowledgeMessages },
  containers: {
    // The Links tab: bound pages on every record type that carries bindings; on a
    // page, the pages that link to it and the records it is bound to.
    "related#groups": { "knowledge.pages": recordPagesGroup() },
    "knowledge.Page#groups": { "knowledge.backlinks": pageBacklinksGroup, "knowledge.bound-to": pageBoundToGroup },
  },
  // The Vault create form, used wherever a vault is created (the wiki's
  // relation-picker inline create resolves it via the model name).
  forms: { "knowledge.Vault": vaultCreateForm },
  icons: {
    knowledge: BookOpen,
    vault: Library,
    note: FileText,
    template: FileStack,
    // `link` now resolves from the base registry (added for the markdown toolbar).
  },
});

export default knowledge;
