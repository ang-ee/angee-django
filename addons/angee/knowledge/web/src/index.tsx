import type { BaseAddonRoute } from "@angee/app";
import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { BookOpen, FileStack, FileText, Library } from "lucide-react";

import { enKnowledgeMessages } from "./i18n";
import { vaultCreateForm } from "./views/vault-form";

const KNOWLEDGE_ID = "knowledge";

const knowledgeRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes("knowledge.home", "/knowledge", lazyRouteComponent(() => import("./views/KnowledgePage"), "KnowledgePage"), undefined, { detailName: "knowledge.page", menu: KNOWLEDGE_ID }),
  {
    // The vaults admin. A static `/knowledge/settings` outranks the
    // `/knowledge/$id` page route, so it is a sibling, not a page id.
    name: "knowledge.settings",
    path: "/knowledge/settings",
    component: lazyRouteComponent(() => import("./views/KnowledgeSettingsPage"), "KnowledgeSettingsPage"),
  },
];

const knowledgeMenu: readonly BaseMenuItem[] = [
  {
    id: KNOWLEDGE_ID,
    label: "Knowledge",
    icon: "knowledge",
    route: "knowledge.home",
    children: [
      { id: "knowledge.home", label: "Wiki", icon: "knowledge", route: "knowledge.home" },
      { id: "knowledge.settings", label: "Vaults", icon: "vault", route: "knowledge.settings" },
    ],
  },
];

// Glyphs the wiki reaches for that the base registry doesn't carry.
const knowledge = defineBaseAddon({
  id: KNOWLEDGE_ID,
  routes: knowledgeRoutes,
  menus: knowledgeMenu,
  i18n: { knowledge: enKnowledgeMessages },
  // The Vault create form, used wherever a vault is created (the wiki's
  // relation-picker inline create resolves it via the model name).
  forms: { Vault: vaultCreateForm },
  icons: {
    knowledge: BookOpen,
    vault: Library,
    note: FileText,
    template: FileStack,
    // `link` now resolves from the base registry (added for the markdown toolbar).
  },
});

export default knowledge;
