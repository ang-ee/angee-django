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

  it("lands the pm root on My Work when it is selected, and selects nothing itself", () => {
    expect(pm.menus?.pm?.home).toBe("projects.my-work");
    expect(pm.shell).toBeUndefined();
  });
});
