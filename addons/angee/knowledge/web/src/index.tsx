import type { BaseAddonRoute, BaseMenuItem } from "@angee/base";
import { defineBaseAddon } from "@angee/base";
import { BookOpen, FileStack, FileText, Library } from "lucide-react";

import { enKnowledgeMessages } from "./i18n";
import { KnowledgePage, PageCrumb } from "./views/KnowledgePage";
import { KnowledgeSettingsPage } from "./views/KnowledgeSettingsPage";
import { vaultCreateForm } from "./views/vault-form";

const KNOWLEDGE_ID = "knowledge";

const knowledgeRoutes: readonly BaseAddonRoute[] = [
  {
    name: "knowledge.home",
    path: "/knowledge",
    shell: "console",
    menu: KNOWLEDGE_ID,
    component: KnowledgePage,
  },
  {
    // The vaults admin. A static `/knowledge/settings` outranks the
    // `/knowledge/$id` page route, so it is a sibling, not a page id.
    name: "knowledge.settings",
    path: "/knowledge/settings",
    shell: "console",
    component: KnowledgeSettingsPage,
  },
  {
    // The page reader nests under the wiki; `KnowledgePage` (the parent) reads
    // the `$id` param and renders that page, so this route carries only the crumb.
    name: "knowledge.page",
    path: "/knowledge/$id",
    shell: "console",
    parent: "knowledge.home",
    crumb: (match) => (
      <PageCrumb id={String((match.params as { id?: string }).id ?? "")} />
    ),
  },
];

const knowledgeMenu: readonly BaseMenuItem[] = [
  {
    id: KNOWLEDGE_ID,
    label: "Knowledge",
    icon: "knowledge",
    group: "platform",
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
