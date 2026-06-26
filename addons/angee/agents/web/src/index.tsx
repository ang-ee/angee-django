import type { BaseAddonRoute } from "@angee/app";
import { defineBaseAddon } from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { Box, Cpu, GitBranch, LayoutTemplate, Server, Sparkles, Wrench } from "lucide-react";

import { enAgentsMessages } from "./i18n";
import { AgentsPage, TemplatesPage } from "./views/AgentsPage";
import { InferenceModelsPage, InferenceProvidersPage } from "./views/InferencePage";
import { McpServersPage, McpToolsPage } from "./views/McpPage";
import { SkillsPage } from "./views/SkillsPage";
import { SourcesPage } from "./views/SourcesPage";

const AGENTS_ID = "agents";

const agentsRoutes: readonly BaseAddonRoute[] = [
  { name: "agents.agents", path: "/agents", layout: "console", component: AgentsPage, resource: "agents.Agent" },
  { name: "agents.agent", path: "/agents/$id", layout: "console", parent: "agents.agents" },
  // Static segments outrank the `/agents/$id` param route.
  { name: "agents.templates", path: "/agents/templates", layout: "console", component: TemplatesPage },
  { name: "agents.template", path: "/agents/templates/$id", layout: "console", parent: "agents.templates" },
  { name: "agents.skills", path: "/agents/skills", layout: "console", component: SkillsPage, resource: "agents.Skill" },
  { name: "agents.sources", path: "/agents/sources", layout: "console", component: SourcesPage },
  { name: "agents.source", path: "/agents/sources/$id", layout: "console", parent: "agents.sources" },
  { name: "agents.mcp-servers", path: "/agents/mcp-servers", layout: "console", component: McpServersPage, resource: "agents.MCPServer" },
  { name: "agents.mcp-server", path: "/agents/mcp-servers/$id", layout: "console", parent: "agents.mcp-servers" },
  { name: "agents.mcp-tools", path: "/agents/mcp-tools", layout: "console", component: McpToolsPage, resource: "agents.MCPTool" },
  { name: "agents.mcp-tool", path: "/agents/mcp-tools/$id", layout: "console", parent: "agents.mcp-tools" },
  { name: "agents.providers", path: "/agents/providers", layout: "console", component: InferenceProvidersPage, resource: "agents.InferenceProvider" },
  { name: "agents.provider", path: "/agents/providers/$id", layout: "console", parent: "agents.providers" },
  { name: "agents.models", path: "/agents/models", layout: "console", component: InferenceModelsPage, resource: "agents.InferenceModel" },
  { name: "agents.model", path: "/agents/models/$id", layout: "console", parent: "agents.models" },
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
    // A domain app (top of the rail) that opts into the settings-style left
    // sub-nav for its grouped sections — they still render as top-bar dropdowns.
    sidebar: true,
    children: [
      {
        id: "agents.menu.agents",
        label: "Agents",
        icon: "agent",
        children: [
          { id: "agents.agents", label: "Agents", icon: "agent", route: "agents.agents" },
          { id: "agents.templates", label: "Templates", icon: "agent-template", route: "agents.templates" },
        ],
      },
      {
        id: "agents.menu.skills",
        label: "Skills",
        icon: "skill",
        children: [
          { id: "agents.skills", label: "Skills", icon: "skill", route: "agents.skills" },
          { id: "agents.sources", label: "Sources", icon: "skill-source", route: "agents.sources" },
        ],
      },
      {
        id: "agents.menu.mcp",
        label: "MCP",
        icon: "mcp-server",
        children: [
          { id: "agents.mcp-servers", label: "Servers", icon: "mcp-server", route: "agents.mcp-servers" },
          { id: "agents.mcp-tools", label: "Tools", icon: "mcp-tool", route: "agents.mcp-tools" },
        ],
      },
      {
        id: "agents.menu.inference",
        label: "Inference",
        icon: "inference-provider",
        children: [
          { id: "agents.providers", label: "Providers", icon: "inference-provider", route: "agents.providers" },
          { id: "agents.models", label: "Models", icon: "inference-model", route: "agents.models" },
        ],
      },
    ],
  },
];

// The side-chatter entry a host (e.g. the notes app) mounts in its chatter "agent" tab,
// bound to the open record, to chat with the user's agent about what they're viewing.
export { AgentChatterPane } from "./views/AgentChatterPane";

const agents = defineBaseAddon({
  id: AGENTS_ID,
  routes: agentsRoutes,
  menus: agentsMenu,
  i18n: { agents: enAgentsMessages },
  icons: {
    // `agent` is a shared glyph owned by the base icon registry — reference it, don't
    // redefine it (the registry is fail-fast on re-registration).
    "agent-template": LayoutTemplate,
    skill: Sparkles,
    "skill-source": GitBranch,
    "mcp-server": Server,
    "mcp-tool": Wrench,
    "inference-provider": Cpu,
    "inference-model": Box,
  },
});

export default agents;
