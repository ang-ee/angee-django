import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import { describe, expect, test } from "vitest";

import work, { QUEUE_MODEL } from "./index";

describe("work addon manifest", () => {
  test("satisfies rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(work)).not.toThrow();
  });

  test("declares queue ownership and resource-free task projections", () => {
    expect((work.routes ?? []).map((route) => route.name)).toEqual([
      "work.queues",
      "work.queues.record",
      "work.board",
      "work.triage",
      "work.cycles",
      "work.cycle-board",
    ]);
    expect(work.routes?.find((route) => route.name === "work.queues")?.resource).toBe(
      QUEUE_MODEL,
    );
    // work.cycles is a parameterized projection — it must NOT claim a
    // resource (a collection href cannot resolve at boot; the framework
    // now fails fast on it).
    expect(
      work.routes?.find((route) => route.name === "work.cycles")?.resource,
    ).toBeUndefined();
    expect(work.routes?.find((route) => route.name === "work.board")?.resource).toBeUndefined();
    expect(
      work.routes?.find((route) => route.name === "work.cycle-board")?.resource,
    ).toBeUndefined();
  });

  test("builds queue projection and cycle-board hrefs", () => {
    const href = createRouteHref(
      (work.routes ?? []).map(({ name, path }) => ({ name, path })),
    );
    expect(href("work.queues.record", { id: "queue 1" })).toBe(
      "/work/queues/queue%201",
    );
    expect(href("work.board", { queueId: "eng/1" })).toBe(
      "/work/queues/eng%2F1/board",
    );
    expect(href("work.triage", { queueId: "eng" })).toBe(
      "/work/queues/eng/triage",
    );
    expect(href("work.cycle-board", { queueId: "eng", id: "cycle 3" })).toBe(
      "/work/queues/eng/cycles/cycle%203",
    );
  });

  test("registers one place, task extensions, and every work glyph", () => {
    expect(work.menus?.[0]?.children?.map((item) => item.route)).toEqual([
      "work.queues",
    ]);
    expect(work.slots?.map((slot) => slot.id)).toEqual([
      "work.task-fields",
      "work.task-triage-actions",
    ]);
    expect(Object.keys(work.icons ?? {}).sort()).toEqual([
      "work-accept",
      "work-board",
      "work-cycle",
      "work-cycle-close",
      "work-decline",
      "work-duplicate",
      "work-queue",
      "work-snooze",
      "work-triage",
    ]);
  });
});
