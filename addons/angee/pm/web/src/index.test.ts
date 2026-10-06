// @vitest-environment happy-dom
import { defineBaseAddon, type BaseAddon } from "@angee/app";
import { captureChrome, expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, it } from "vitest";

import pm from "./index";

describe("angee.pm", () => {
  it("is a valid addon whose own menu ids stay in its namespace", () => {
    expectValidBaseAddon(pm);
    const own = Object.keys(pm.menus ?? {}).filter((id) => id === "pm" || id.startsWith("pm."));
    expect(own).toEqual(["pm"]);
  });

  it("flattens the PM apps into one rail and removes the duplicate assignee board", () => {
    expect(pm.menus?.pm?.include).toEqual([
      { id: "projects", flatten: true },
      { id: "work", flatten: true },
      { id: "portfolio", flatten: true },
      { id: "proposals", flatten: true },
    ]);
    expect(pm.menus?.["projects.board"]).toEqual({ remove: true });
  });

  it("lands the pm root on My Work when it is selected, and selects nothing itself", () => {
    expect(pm.menus?.pm?.home).toBe("projects.my-work");
    expect(pm.shell).toBeUndefined();
  });
});

const Page = () => null;
// Minimal upstream contracts: the ids, routes and anchors the suite rearranges, without importing peer pages.
function suite(triagePath: string): readonly BaseAddon[] {
  const upstream = [
    defineBaseAddon({ id: "projects",
      routes: [{ name: "projects.my-work", path: "/projects/my-work", layout: "console", component: Page }],
      menus: [{ id: "projects", children: [
        { id: "projects.my-work", label: "My work", route: "projects.my-work" },
        { id: "projects.projects", label: "Projects" },
        { id: "projects.tasks", label: "Tasks" },
        { id: "projects.board", label: "Board" },
      ] }] }),
    defineBaseAddon({ id: "work",
      i18n: { work: { "queue.settings": "Queue settings" } },
      routes: [
        { name: "work.queues", path: "/work/queues", layout: "console", component: Page },
        { name: "work.queues.record", path: "/work/queues/$id", parent: "work.queues", component: Page },
        { name: "work.triage-hub", path: "/work/triage", layout: "console", component: Page },
        { name: "work.triage", path: triagePath, layout: "console", menu: "work.triage-hub", component: Page },
      ],
      menus: [{ id: "work", children: [
        { id: "work.queues", label: "Queues", route: "work.queues" },
        { id: "work.triage-hub", label: "Triage", route: "work.triage-hub" },
        { id: "work.boards-hub", label: "Boards" },
        { id: "work.cycles-hub", label: "Cycles" },
      ] }] }),
    defineBaseAddon({ id: "portfolio", menus: [{ id: "portfolio", children: [
      { id: "portfolio.roadmap", label: "Roadmap" },
      { id: "portfolio.products", label: "Products" },
      { id: "portfolio.initiatives", label: "Initiatives" },
    ] }] }),
    defineBaseAddon({ id: "proposals", menus: [{ id: "proposals", children: [
      { id: "proposals.rounds", label: "Rounds" },
      { id: "proposals.proposals", label: "Responses" },
    ] }] }),
  ];
  return [...upstream, { ...pm, dependsOn: upstream.map(({ id }) => id) }];
}

describe("team configuration in Settings, team work in the suite", () => {
  it("keeps a team's triage in the suite while the team's record sits in Settings", async () => {
    for (const [path, place] of [
      ["/work/triage/q1", { item: "work.triage-hub", scope: "apps", root: "pm" }],
      ["/work/queues/q1", { item: "work.queues", scope: "settings", root: "work.queues" }],
    ] as const) {
      const captured = await captureChrome({ addons: suite("/work/triage/$queueId"), path, resources: ["work.Queue"] });
      try {
        expect(captured.props().place).toEqual(place);
      } finally {
        captured.cleanup();
      }
    }
  });

  it("refuses a team page whose path nests under the team record it lifted into Settings", async () => {
    await expect(captureChrome({
      addons: suite("/work/queues/$queueId/triage"), path: "/work/queues/q1/triage", resources: ["work.Queue"],
    })).rejects.toThrow(
      /Route "work\.triage" is anchored to menu item "work\.triage-hub" in app "pm", but its path "\/work\/queues\/\$queueId\/triage" nests under menu item "work\.queues" in Settings/,
    );
  });
});
