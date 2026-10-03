import { describe, expect, it } from "vitest";
import { expectValidBaseAddon } from "@angee/app/testing";

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

  it("declares its own perspective, home and brand as the suite's defaults", () => {
    expect(pm.shell).toEqual({ home: "projects.my-work", brand: { name: "Angee PM", mark: "pm" }, perspective: "pm" });
    expect(pm.perspectives).toEqual({ pm: { root: "pm", home: "projects.my-work" } });
  });
});
