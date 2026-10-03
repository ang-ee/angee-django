import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import { describe, expect, test } from "vitest";

import proposals, { PROPOSAL_MODEL, ROUND_MODEL } from ".";

describe("proposals addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(proposals)).not.toThrow();
  });

  test("owns round/proposal records and one addon-local comparison route", () => {
    expect((proposals.routes ?? []).map((route) => route.name)).toEqual([
      "proposals.rounds",
      "proposals.rounds.record",
      "proposals.round-comparison",
      "proposals.proposals",
      "proposals.proposals.record",
    ]);
    expect(
      proposals.routes?.find((route) => route.name === "proposals.rounds")
        ?.resource,
    ).toBe(ROUND_MODEL);
    expect(
      proposals.routes?.find((route) => route.name === "proposals.proposals")
        ?.resource,
    ).toBe(PROPOSAL_MODEL);
    expect(
      proposals.routes?.find(
        (route) => route.name === "proposals.round-comparison",
      )?.resource,
    ).toBeUndefined();
  });

  test("builds record and comparison hrefs from route declarations", () => {
    const href = createRouteHref(
      (proposals.routes ?? []).map(({ name, path }) => ({ name, path })),
    );
    expect(href("proposals.rounds.record", { id: "rnd 1" })).toBe(
      "/proposals/rounds/rnd%201",
    );
    expect(href("proposals.round-comparison", { id: "rnd/1" })).toBe(
      "/proposals/rounds/rnd%2F1/compare",
    );
    expect(href("proposals.proposals.record", { id: "prp 1" })).toBe(
      "/proposals/responses/prp%201",
    );
  });

  test("contributes one place, both item panes, and prefixed glyphs", () => {
    expect(proposals.menus).toHaveLength(1);
    expect(proposals.menus?.[0]?.children?.map((item) => item.route)).toEqual([
      "proposals.rounds",
      "proposals.proposals",
    ]);
    expect(containerChildren(proposals.containers).map(([id]) => id)).toEqual([
      "proposals.responder",
      "proposals.opening",
      "proposals.people-rail",
      "proposals.round-open",
      "proposals.project-round-open",
      "proposals.round-verbs",
      "proposals.project-round-verbs",
      "proposals.round-people",
      "proposals.project-people",
      "proposals.project-approach",
      "proposals.project-rounds",
      "proposals.task-responder-share",
      "proposals.task-rounds",
    ]);
    // The round's Share panel role and opening policy ride iam's model-scoped containers.
    expect(Object.keys(proposals.containers ?? {}).slice(0, 2)).toEqual([`${ROUND_MODEL}#access-roles`, `${ROUND_MODEL}#access-visibility`]);
    expect(Object.keys(proposals.icons ?? {}).sort()).toEqual([
      "proposals-award",
      "proposals-open",
      "proposals-publish",
      "proposals-response",
      "proposals-round",
      "proposals-submit",
      "proposals-transfer",
      "proposals-withdraw",
    ]);
  });
});

/** The manifest's container children, keyed by id, in declaration order. */
function containerChildren(containers: object | undefined): [string, { content?: unknown }][] {
  return Object.values(containers ?? {}).flatMap((entry) => (Array.isArray(entry) ? entry : [entry]) as Record<string, unknown>[])
    .flatMap((entry) => Object.entries(entry).filter(([id]) => id.includes(".")) as [string, { content?: unknown }][]);
}
