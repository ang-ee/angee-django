import { defineAddon } from "@angee/app";
import { LayoutList } from "lucide-react";

/**
 * The project-management suite: the PM apps arranged as one Linear-like rail.
 * A product layered on the suite overrides its shell and narrows its rail.
 */
export default defineAddon({
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
    "portfolio.initiatives": { sequence: 75 },
    "proposals.rounds": { sequence: 80 },
    "proposals.proposals": { sequence: 85 },
    // Teams are queues; the suite manages them in the Settings place.
    "work.queues": { label: "Teams", group: "platform" },
    // My Work already lists my assigned tasks; products are a portfolio admin view.
    "projects.board": { hide: true },
    "portfolio.products": { hide: true },
  },
  vocabulary: [{ app: "pm", resources: { "work.Queue": { label: "Team", pluralLabel: "Teams" } } }],
  perspectives: { pm: { root: "pm", home: "projects.my-work" } },
  shell: { home: "projects.my-work", brand: { name: "Angee PM", mark: "pm" }, perspective: "pm" },
});
