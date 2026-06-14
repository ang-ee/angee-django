import type { BaseAddonRoute, BaseMenuItem } from "@angee/base";
import { defineBaseAddon } from "@angee/base";
import { Box, Cpu, GitBranch, LayoutTemplate, Server, Sparkles, Wrench } from "lucide-react";

import { enAgentsMessages } from "./i18n";
import { AgentsPage, TemplatesPage } from "./views/AgentsPage";
import { InferenceModelsPage, InferenceProvidersPage } from "./views/InferencePage";
import { McpServersPage, McpToolsPage } from "./views/McpPage";
import { SkillsPage } from "./views/SkillsPage";
import { SourcesPage } from "./views/SourcesPage";

const AGENTS_ID = "agents";

const agentsRoutes: readonly BaseAddonRoute[] = [
  { name: "agents.agents", path: "/agents", shell: "console", component: AgentsPage },
  { name: "agents.agent", path: "/agents/$id", shell: "console", parent: "agents.agents" },
  // Static segments outrank the `/agents/$id` param route.
  { name: "agents.templates", path: "/agents/templates", shell: "console", component: TemplatesPage },
  { name: "agents.template", path: "/agents/templates/$id", shell: "console", parent: "agents.templates" },
  { name: "agents.skills", path: "/agents/skills", shell: "console", component: SkillsPage },
  { name: "agents.sources", path: "/agents/sources", shell: "console", component: SourcesPage },
  { name: "agents.source", path: "/agents/sources/$id", shell: "console", parent: "agents.sources" },
  { name: "agents.mcp-servers", path: "/agents/mcp-servers", shell: "console", component: McpServersPage },
  { name: "agents.mcp-server", path: "/agents/mcp-servers/$id", shell: "console", parent: "agents.mcp-servers" },
  { name: "agents.mcp-tools", path: "/agents/mcp-tools", shell: "console", component: McpToolsPage },
  { name: "agents.mcp-tool", path: "/agents/mcp-tools/$id", shell: "console", parent: "agents.mcp-tools" },
  { name: "agents.providers", path: "/agents/providers", shell: "console", component: InferenceProvidersPage },
  { name: "agents.provider", path: "/agents/providers/$id", shell: "console", parent: "agents.providers" },
  { name: "agents.models", path: "/agents/models", shell: "console", component: InferenceModelsPage },
  { name: "agents.model", path: "/agents/models/$id", shell: "console", parent: "agents.models" },
];

// One rail (app) icon for the addon; its children are the top-bar menus, and a
// child that itself has children renders as a dropdown (see chrome `TopMenu`).
const agentsMenu: readonly BaseMenuItem[] = [
  {
    // Route-less app root: the rail icon resolves its target through the first
    // descendant with one (here `agents.agents` → `/agents`), so the route is
    // referenced by a single menu item and needs no chrome disambiguation.
    id: AGENTS_ID,
    label: "Agents",
    icon: "agent",
    group: "platform",
    children: [
      {
        id: "agents.menu.agents",
        label: "Agents",
        icon: "agent",
        children: [
          { id: "agents.agents", label: "Agents", icon: "agent", route: "agents.agents" },
          { id: "agents.templates", label: "Templates", icon: "agentTemplate", route: "agents.templates" },
        ],
      },
      {
        id: "agents.menu.skills",
        label: "Skills",
        icon: "skill",
        children: [
          { id: "agents.skills", label: "Skills", icon: "skill", route: "agents.skills" },
          { id: "agents.sources", label: "Sources", icon: "skillSource", route: "agents.sources" },
        ],
      },
      {
        id: "agents.menu.mcp",
        label: "MCP",
        icon: "mcpServer",
        children: [
          { id: "agents.mcp-servers", label: "Servers", icon: "mcpServer", route: "agents.mcp-servers" },
          { id: "agents.mcp-tools", label: "Tools", icon: "mcpTool", route: "agents.mcp-tools" },
        ],
      },
      {
        id: "agents.menu.inference",
        label: "Inference",
        icon: "inferenceProvider",
        children: [
          { id: "agents.providers", label: "Providers", icon: "inferenceProvider", route: "agents.providers" },
          { id: "agents.models", label: "Models", icon: "inferenceModel", route: "agents.models" },
        ],
      },
    ],
  },
];

const agents = defineBaseAddon({
  id: AGENTS_ID,
  routes: agentsRoutes,
  menus: agentsMenu,
  i18n: { agents: enAgentsMessages },
  icons: {
    // `agent` is a shared glyph owned by the base icon registry — reference it, don't
    // redefine it (the registry is fail-fast on re-registration).
    agentTemplate: LayoutTemplate,
    skill: Sparkles,
    skillSource: GitBranch,
    mcpServer: Server,
    mcpTool: Wrench,
    inferenceProvider: Cpu,
    inferenceModel: Box,
  },
});

export default agents;
