import type { BaseAddonRoute } from "@angee/app";
import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Box, Cpu, GitBranch, LayoutTemplate, Server, Sparkles, Wrench } from "lucide-react";

import { enAgentsMessages } from "./i18n";
import { AgentChatterPane } from "./views/AgentChatterPane";

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
          // Reuse the registered `comments` glyph — no new icon registration.
          { id: "agents.sessions", label: "Sessions", icon: "comments", route: "agents.sessions" },
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

// The side-chatter entry the addon contributes as the global chatter "agents" tab (below),
// bound to the active page's view, to chat with the user's agent about what they're viewing.
export { AgentChatterPane };

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
  chatter: [
    {
      id: "agents",
      sequence: 0,
      label: "Agents",
      icon: "agent",
      render: ({ view }) => <AgentChatterPane view={view} />,
    },
  ],
});

export default agents;
