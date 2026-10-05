import { defineBaseAddon } from "@angee/app";
import { LayoutList } from "lucide-react";

/**
 * The project-management suite: the PM apps arranged as one Linear-like rail.
 * Installing it only rearranges the rail; the rest of the console is unchanged.
 * It declares the `pm` perspective but does not select it: a product built on
 * the suite selects it (with its own home and brand) in its `shell`, or a
 * deployment selects it through `ANGEE_UI`.
 */
export default defineBaseAddon({
  id: "pm",
  icons: { pm: LayoutList },
  menus: {
    pm: {
      label: "Work",
      icon: "pm",
      include: [
        { id: "projects", flatten: true },
        { id: "work", flatten: true },
        { id: "portfolio", flatten: true },
        { id: "proposals", flatten: true },
      ],
    },
    "projects.my-work": { sequence: 10 },
    "work.triage-hub": { sequence: 20 },
    "projects.projects": { sequence: 30 },
    "projects.tasks": { sequence: 40 },
    "work.boards-hub": { sequence: 50 },
    "work.cycles-hub": { sequence: 60 },
    "portfolio.roadmap": { sequence: 70 },
    "proposals.rounds": { sequence: 80 },
    // Teams are queues; the suite manages them in the Settings place.
    "work.queues": { group: "platform" },
    // My Work lists my assigned tasks, so the assignee board goes.
    "projects.board": { remove: true },
    // Reached from Roadmap and Proposals rather than the rail.
    "portfolio.products": { hide: true },
    "portfolio.initiatives": { hide: true },
    "proposals.proposals": { hide: true },
  },
  vocabulary: [{
    app: "pm",
    resources: { "work.Queue": { label: "Team", pluralLabel: "Teams" } },
    menus: { "work.queues": "Teams", "proposals.rounds": "Proposals" },
  }],
  perspectives: { pm: { root: "pm", home: "projects.my-work" } },
});
