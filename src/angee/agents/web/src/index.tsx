import type { BaseAddonRoute } from "@angee/app";
import { defineBaseAddon, resourcePageRoutes, type MenuDeclarations } from "@angee/app";
import { useActiveChatterView } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Box, Cpu, GitBranch, LayoutTemplate, Server, Sparkles, Wrench } from "lucide-react";

import { enAgentsMessages } from "./i18n";
import { AgentChatterPane } from "./views/AgentChatterPane";
import { inferenceProviderForm } from "./views/InferenceProviderForm";

const AGENTS_ID = "agents";

const agentsRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes("agents.agents", "/agents", lazyRouteComponent(() => import("./views/AgentsPage"), "AgentsPage"), "agents.Agent", { detailName: "agents.agent" }),
  // Static segments outrank the `/agents/$id` param route.
  ...resourcePageRoutes("agents.templates", "/agents/templates", lazyRouteComponent(() => import("./views/AgentsPage"), "TemplatesPage"), undefined, { detailName: "agents.template" }),
  // The full-page sessions view. The `$id` child carries no component — it is the URL
  // placeholder the parent renders through, so the parent stays mounted across `:id`
  // changes (the substrate keep-alive needs). Static `/agents/sessions` outranks `/agents/$id`.
  ...resourcePageRoutes("agents.sessions", "/agents/sessions", lazyRouteComponent(() => import("./views/AgentSessionsPage"), "AgentSessionsPage"), undefined, { detailName: "agents.session" }),
  { name: "agents.skills", path: "/agents/skills", component: lazyRouteComponent(() => import("./views/SkillsPage"), "SkillsPage"), resource: "agents.Skill" },
  ...resourcePageRoutes("agents.sources", "/agents/sources", lazyRouteComponent(() => import("./views/SourcesPage"), "SourcesPage"), undefined, { detailName: "agents.source" }),
  ...resourcePageRoutes("agents.mcp-servers", "/agents/mcp-servers", lazyRouteComponent(() => import("./views/McpPage"), "McpServersPage"), "agents.MCPServer", { detailName: "agents.mcp-server" }),
  ...resourcePageRoutes("agents.mcp-tools", "/agents/mcp-tools", lazyRouteComponent(() => import("./views/McpPage"), "McpToolsPage"), "agents.MCPTool", { detailName: "agents.mcp-tool" }),
  ...resourcePageRoutes("agents.providers", "/agents/providers", lazyRouteComponent(() => import("./views/InferencePage"), "InferenceProvidersPage"), "agents.InferenceProvider", { detailName: "agents.provider" }),
  ...resourcePageRoutes("agents.models", "/agents/models", lazyRouteComponent(() => import("./views/InferencePage"), "InferenceModelsPage"), "agents.InferenceModel", { detailName: "agents.model" }),
];

// The addon owns a domain Agents root and a separate AI Settings category. Their
// leaves retain the same routes; only their menu ownership changes.
const agentsMenu: MenuDeclarations = {
  [AGENTS_ID]: { label: "Agents", icon: "agent" },
  "agents.menu.agents": { label: "Agents", icon: "agent", parent: AGENTS_ID },
  "agents.agents": { label: "Agents", icon: "agent", route: "agents.agents", parent: "agents.menu.agents" },
  "agents.templates": { label: "Templates", icon: "agent-template", route: "agents.templates", parent: "agents.menu.agents" },
  "agents.sessions": { label: "Sessions", icon: "comments", route: "agents.sessions", parent: "agents.menu.agents" },
  "agents.menu.skills": { label: "Skills", icon: "skill", parent: AGENTS_ID },
  "agents.skills": { label: "Skills", icon: "skill", route: "agents.skills", parent: "agents.menu.skills" },
  "agents.sources": { label: "Sources", icon: "skill-source", route: "agents.sources", parent: "agents.menu.skills" },
  "agents.menu.mcp": { label: "MCP", icon: "mcp-server", parent: AGENTS_ID },
  "agents.mcp-servers": { label: "Servers", icon: "mcp-server", route: "agents.mcp-servers", parent: "agents.menu.mcp" },
  "agents.mcp-tools": { label: "Tools", icon: "mcp-tool", route: "agents.mcp-tools", parent: "agents.menu.mcp" },
  "agents.ai": { label: "AI", icon: "inference-provider", group: "platform" },
  "agents.providers": { label: "Providers", icon: "inference-provider", route: "agents.providers", parent: "agents.ai" },
  "agents.models": { label: "Models", icon: "inference-model", route: "agents.models", parent: "agents.ai" },
};

// The shell drawer keeps its sessions mounted while the active view changes.
export { AgentChatterPane };

const agents = defineBaseAddon({
  id: AGENTS_ID,
  routes: agentsRoutes,
  menus: agentsMenu,
  i18n: { agents: enAgentsMessages },
  forms: { "agents.InferenceProvider": inferenceProviderForm },
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
  containers: {
    "shell#drawers-right": {
      "agents.chat": {
        sequence: 10,
        content: { title: "Agents", icon: "agent", render: () => <AgentDrawer /> },
      },
    },
  },
});

export default agents;

function AgentDrawer() {
  const { view } = useActiveChatterView();
  return <AgentChatterPane view={view} />;
}
