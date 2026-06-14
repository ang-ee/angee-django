import type { BaseAddon, BaseAddonRoute, BaseMenuItem } from "@angee/base";
import { Bot, Box, Cpu, LayoutTemplate, Server, Sparkles, Wrench } from "lucide-react";

import { AgentsPage, TemplatesPage } from "./views/AgentsPage";
import { InferenceModelsPage, InferenceProvidersPage } from "./views/InferencePage";
import { McpServersPage, McpToolsPage } from "./views/McpPage";
import { SkillsPage } from "./views/SkillsPage";

const AGENTS_ID = "agents";

const agentsRoutes: readonly BaseAddonRoute[] = [
  { name: "agents.agents", path: "/agents", shell: "console", component: AgentsPage },
  { name: "agents.agent", path: "/agents/$id", shell: "console", parent: "agents.agents" },
  // Static segments outrank the `/agents/$id` param route.
  { name: "agents.templates", path: "/agents/templates", shell: "console", component: TemplatesPage },
  { name: "agents.template", path: "/agents/templates/$id", shell: "console", parent: "agents.templates" },
  { name: "agents.skills", path: "/agents/skills", shell: "console", component: SkillsPage },
  { name: "agents.mcp-servers", path: "/agents/mcp-servers", shell: "console", component: McpServersPage },
  { name: "agents.mcp-server", path: "/agents/mcp-servers/$id", shell: "console", parent: "agents.mcp-servers" },
  { name: "agents.mcp-tools", path: "/agents/mcp-tools", shell: "console", component: McpToolsPage },
  { name: "agents.mcp-tool", path: "/agents/mcp-tools/$id", shell: "console", parent: "agents.mcp-tools" },
  { name: "agents.providers", path: "/agents/providers", shell: "console", component: InferenceProvidersPage },
  { name: "agents.provider", path: "/agents/providers/$id", shell: "console", parent: "agents.providers" },
  { name: "agents.models", path: "/agents/models", shell: "console", component: InferenceModelsPage },
  { name: "agents.model", path: "/agents/models/$id", shell: "console", parent: "agents.models" },
];

const agentsMenu: readonly BaseMenuItem[] = [
  {
    // Route-less group root: the rail icon inherits its target from the first child.
    id: AGENTS_ID,
    label: "Agents",
    icon: "agent",
    group: "platform",
    children: [
      { id: "agents.agents", label: "Agents", icon: "agent", route: "agents.agents" },
      { id: "agents.templates", label: "Templates", icon: "agentTemplate", route: "agents.templates" },
    ],
  },
  {
    id: "agents.skills.group",
    label: "Skills",
    icon: "skill",
    group: "platform",
    children: [{ id: "agents.skills", label: "Skills", icon: "skill", route: "agents.skills" }],
  },
  {
    id: "agents.mcp",
    label: "MCP",
    icon: "mcpServer",
    group: "platform",
    children: [
      { id: "agents.mcp-servers", label: "Servers", icon: "mcpServer", route: "agents.mcp-servers" },
      { id: "agents.mcp-tools", label: "Tools", icon: "mcpTool", route: "agents.mcp-tools" },
    ],
  },
  {
    id: "agents.inference",
    label: "Inference",
    icon: "inferenceProvider",
    group: "platform",
    children: [
      { id: "agents.providers", label: "Providers", icon: "inferenceProvider", route: "agents.providers" },
      { id: "agents.models", label: "Models", icon: "inferenceModel", route: "agents.models" },
    ],
  },
];

const agents: BaseAddon = {
  id: AGENTS_ID,
  routes: agentsRoutes,
  menus: agentsMenu,
  icons: {
    agent: Bot,
    agentTemplate: LayoutTemplate,
    skill: Sparkles,
    mcpServer: Server,
    mcpTool: Wrench,
    inferenceProvider: Cpu,
    inferenceModel: Box,
  },
};

export default agents;
