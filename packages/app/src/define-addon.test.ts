import { describe, expect, test } from "vitest";
import { STATUS_TONES } from "@angee/ui/widgets/status-tones";

import { resolveContainer } from "@angee/ui/runtime";

import { composeAddons, defineAddon } from "./define-addon";

const IDENTITY_CANONICALIZER = {
  canonicalModelLabel: (spelling: string) => spelling,
};
const FORM = { resource: "notes.Note", Component: () => null };
const FORM_A = { resource: "Note", Component: () => null };
const FORM_B = { resource: "notes.Note", Component: () => null };
const ids = (children: readonly { id: string }[]): string[] => children.map((child) => child.id);

describe("defineAddon", () => {
  test("returns the manifest unchanged for typed authoring", () => {
    const manifest = defineAddon({ id: "notes" });
    expect(manifest).toEqual({ id: "notes" });
  });
});

describe("composeAddons", () => {
  test("normalizes shipped views and rejects duplicate or foreign ids", () => {
    const preset = { id: "desk.open", resource: "Note", label: "Open", fixedFilter: { title: { exact: "Open" } } };
    const addon = defineAddon({ id: "desk", resourceViews: [preset] });
    const canonicalizeModel = () => "notes.Note";
    expect(composeAddons([addon], { canonicalModelLabel: canonicalizeModel }).resourceViews["desk.open"])
      .toEqual({ ...preset, resource: "notes.Note", preset: "desk.open" });
    expect(() => composeAddons([defineAddon({ id: "desk", resourceViews: [preset, preset] })], { canonicalModelLabel: canonicalizeModel }))
      .toThrow(/resource view/);
    expect(() => composeAddons([defineAddon({ id: "other", resourceViews: [preset] })], { canonicalModelLabel: canonicalizeModel }))
      .toThrow(/namespace/);
  });
  test("composes normalized status vocabulary and rejects duplicate claims", () => {
    const a = defineAddon({ id: "a", statusTones: { " REVIEWED ": "success" } });
    const b = defineAddon({ id: "b", statusTones: { queued: "warning" } });
    expect(composeAddons([a, b], IDENTITY_CANONICALIZER).statusTones).toEqual({ reviewed: "success", queued: "warning" });
    expect(() => composeAddons([a, { id: "collision", statusTones: { reviewed: "success" } }], IDENTITY_CANONICALIZER))
      .toThrow(/status tone "reviewed"/);
    expect(() => composeAddons([{ id: "bad", statusTones: { " ": "neutral" } }], IDENTITY_CANONICALIZER))
      .toThrow(/empty status tone key/);
  });

  test.each(Object.values(STATUS_TONES).flat())("rejects a claim on framework status %s", (value) => {
    const addon = defineAddon({ id: "collision", statusTones: { [` ${value.toUpperCase()} `]: "accent" } });
    expect(() => composeAddons([addon], IDENTITY_CANONICALIZER))
      .toThrow(`redefines framework status tone "${value}"`);
  });

  test("composes addon record detail keys in deterministic order", () => {
    const a = defineAddon({ id: "a", recordSearchKeys: ["detailA"] });
    const b = defineAddon({ id: "b", recordSearchKeys: ["detailB"] });
    expect(composeAddons([b, a], IDENTITY_CANONICALIZER).recordSearchKeys).toEqual(["detailA", "detailB"]);
    expect(() => composeAddons([a, { id: "collision", recordSearchKeys: ["detailA"] }], IDENTITY_CANONICALIZER))
      .toThrow(/record search key/);
  });

  test.each(["", "recordTab", "recordNav"])("rejects empty and framework-owned record keys: %s", (key) => {
    expect(() => composeAddons([{ id: "bad", recordSearchKeys: [key] }], IDENTITY_CANONICALIZER))
      .toThrow(/reserved or empty record search key/);
  });

  test("concatenates routes in addon order", () => {
    const a = defineAddon({ id: "a", routes: [{ name: "a.home", path: "/a", layout: "console" }] });
    const b = defineAddon({ id: "b", routes: [{ name: "b.home", path: "/b", layout: "console" }] });
    expect(
      composeAddons([a, b], IDENTITY_CANONICALIZER).routes.map((r) => r.path),
    ).toEqual(["/a", "/b"]);
  });

  test("rejects two routes that declare the same name", () => {
    const a = defineAddon({ id: "a", routes: [{ name: "dup", path: "/a", layout: "console" }] });
    const b = defineAddon({ id: "b", routes: [{ name: "dup", path: "/b", layout: "console" }] });
    expect(() => composeAddons([a, b], IDENTITY_CANONICALIZER)).toThrow(/dup/);
  });

  test("rejects duplicate menu item ids across nested menus", () => {
    const a = defineAddon({
      id: "a",
      menus: [
        {
          id: "root",
          label: "Root",
          children: [{ id: "dup", label: "Dup" }],
        },
      ],
    });
    const b = defineAddon({ id: "b", menus: [{ id: "dup", label: "Dup" }] });

    expect(() => composeAddons([a, b], IDENTITY_CANONICALIZER)).toThrow(
      /menu item id "dup"/,
    );
  });

  test("defaults menu item id to route before claiming collisions", () => {
    const composed = composeAddons([
      defineAddon({
        id: "notes",
        menus: [
          {
            label: "Notes",
            route: "notes.home",
            children: [{ label: "Archive", route: "notes.archive" }],
          },
        ],
      }),
    ], IDENTITY_CANONICALIZER);

    expect(composed.menus[0]?.id).toBe("notes.home");
    expect(composed.menus[0]?.children?.[0]?.id).toBe("notes.archive");
    expect(() =>
      composeAddons([
        defineAddon({ id: "a", menus: [{ route: "shared.route" }] }),
        defineAddon({ id: "b", menus: [{ id: "shared.route" }] }),
      ], IDENTITY_CANONICALIZER),
    ).toThrow(/menu item id "shared.route"/);
  });

  test("preserves route params while composing cross-addon menu targets", () => {
    const composed = composeAddons(
      [
        defineAddon({
          id: "document-review",
          menus: [
            {
              route: "dashboards.addon",
              params: { key: "example.document_review.overview" },
            },
          ],
        }),
      ],
      IDENTITY_CANONICALIZER,
    );

    expect(composed.menus).toEqual([
      {
        id: "dashboards.addon",
        route: "dashboards.addon",
        params: { key: "example.document_review.overview" },
      },
    ]);
  });

  test("requires a menu id when no route can own the default", () => {
    expect(() =>
      composeAddons(
        [defineAddon({ id: "bad", menus: [{ label: "Bad" }] })],
        IDENTITY_CANONICALIZER,
      ),
    ).toThrow(/without id or route/);
  });

  test("merges widget and i18n registries", () => {
    const a = defineAddon({
      id: "a",
      widgets: { text: "TEXT" },
      i18n: { notes: { title: "Title" } },
    });
    const b = defineAddon({ id: "b", widgets: { date: "DATE" } });
    const composed = composeAddons([a, b], IDENTITY_CANONICALIZER);
    expect(composed.widgets).toEqual({ text: "TEXT", date: "DATE" });
    expect(composed.i18n).toEqual({ notes: { title: "Title" } });
  });

  test("orders aside tabs by sequence, not addon order", () => {
    const a = defineAddon({
      id: "a",
      containers: { "record#aside": { "a.late": { sequence: 20, content: { label: "Late" } } } },
    });
    const b = defineAddon({
      id: "b",
      containers: { "record#aside": { "b.early": { sequence: 10, content: { label: "Early" } } } },
    });
    const composed = composeAddons([a, b], IDENTITY_CANONICALIZER);
    expect(ids(resolveContainer(composed.containers, "record#aside")).filter((id) => !id.startsWith("chatter."))).toEqual([
      "b.early",
      "a.late",
    ]);
  });

  test("addresses model tabs per model; the address carries the model, so one id may sit on two models", () => {
    const history = { content: { label: "History" } };
    const composed = composeAddons([defineAddon({
      id: "history",
      containers: {
        "parties.Party#aside": { "history.party": history },
        "parties.Circle#aside": { "history.circle": history },
      },
    })], IDENTITY_CANONICALIZER);
    expect(Object.keys(composed.containers.children).filter((address) => address.includes("."))).toEqual([
      "parties.Party#aside",
      "parties.Circle#aside",
    ]);
    expect(ids(resolveContainer(composed.containers, "record#aside", { models: ["parties.Party"] }))).toContain("history.party");
    expect(ids(resolveContainer(composed.containers, "record#aside", { models: ["parties.Party"] }))).not.toContain("history.circle");
    const both = composeAddons([defineAddon({
      id: "history",
      containers: { "parties.Party#aside": { "history.history": history }, "parties.Circle#aside": { "history.history": history } },
    })], IDENTITY_CANONICALIZER);
    expect(ids(resolveContainer(both.containers, "record#aside", { models: ["parties.Circle"] }))).toContain("history.history");
    // The kind's own address renders with every model's, so there one id is one child.
    expect(() => composeAddons([defineAddon({
      id: "history",
      containers: { "record#aside": { "history.history": history }, "parties.Circle#aside": { "history.history": history } },
    })], IDENTITY_CANONICALIZER)).toThrow(/"history.history" at "record#aside" and "parties.Circle#aside", which render together; one id per container/);
  });

  test("an addon declares children only in its namespace and alters only its dependencies' children", () => {
    // Silently letting addon array order pick the winner hid a real clash (the
    // integrate/whatsapp record-verb ids): child ids are namespaced by their
    // declaring addon, and a change to another addon's child needs a dependency.
    const a = defineAddon({ id: "a", containers: { "shell#notices": { "a.logo": { sequence: 1, content: "A" } } } });
    expect(() => composeAddons([a, defineAddon({
      id: "b",
      containers: { "shell#notices": { "a.logo": { sequence: 2, content: "B" } } },
    })], IDENTITY_CANONICALIZER)).toThrow(/outside its namespace/);
    expect(() => composeAddons([a, defineAddon({
      id: "b",
      containers: { "shell#notices": { "a.logo": { sequence: 2 } } },
    })], IDENTITY_CANONICALIZER)).toThrow(/does not depend on/);
  });

  test("canonicalizes route, form, and model container addresses before merging", () => {
    const canonical = (spelling: string) =>
      spelling === "Note" || spelling === "note" || spelling === "legacy.Note" ? "notes.Note" : spelling;
    const composed = composeAddons([
      defineAddon({
        id: "notes",
        routes: [{ name: "notes.home", path: "/notes", resource: "Note" }],
        forms: { note: FORM },
        containers: {
          "legacy.Note#aside": { "notes.history": { content: { label: "History" } } },
          "legacy.Note#sections": { "notes.extra": { content: null } },
        },
      }),
    ], { canonicalModelLabel: canonical });

    expect(composed.routes[0]?.resource).toBe("notes.Note");
    expect(composed.forms).toEqual({ "notes.Note": FORM });
    expect(composed.containers.children["notes.Note#aside"]?.map((child) => child.id)).toEqual(["notes.history"]);
    expect(composed.containers.children["notes.Note#sections"]?.[0]).toMatchObject({
      id: "notes.extra",
      owner: "notes",
      address: "notes.Note#sections",
    });
  });

  test("detects registry collisions after model aliases canonicalize", () => {
    const canonical = (spelling: string) =>
      spelling === "Note" ? "notes.Note" : spelling;

    expect(() =>
      composeAddons([
        defineAddon({ id: "a", forms: { Note: FORM_A } }),
        defineAddon({ id: "b", forms: { "notes.Note": FORM_B } }),
      ], { canonicalModelLabel: canonical }),
    ).toThrow(/form override "notes\.Note"/);
  });

  test("rejects a complete form registered under a different resource", () => {
    expect(() =>
      composeAddons([
        defineAddon({
          id: "bad",
          forms: {
            "notes.Note": { resource: "tasks.Task", Component: () => null },
          },
        }),
      ], IDENTITY_CANONICALIZER),
    ).toThrow(/component resource "tasks\.Task"/);
  });

  test("the same child id under a different container is kept separate", () => {
    const a = defineAddon({
      id: "a",
      containers: {
        "shell#notices": { "a.logo": { content: "notice" } },
        "shell#user-menu": { "a.logo": { content: "item" } },
      },
    });
    const { containers } = composeAddons([a], IDENTITY_CANONICALIZER);
    // Beside the framework's own children (`chrome.view-as`, `chrome.developer-mode`).
    expect(ids(resolveContainer(containers, "shell#notices")).filter((id) => id.startsWith("a."))).toEqual(["a.logo"]);
    expect(ids(resolveContainer(containers, "shell#user-menu")).filter((id) => id.startsWith("a."))).toEqual(["a.logo"]);
  });

  test("orders drawers by sequence, not addon order", () => {
    const a = defineAddon({
      id: "a",
      containers: { "shell#drawers-bottom": { "a.late": { sequence: 20, content: { title: "Late", render: () => null } } } },
    });
    const b = defineAddon({
      id: "b",
      containers: { "shell#drawers-bottom": { "b.early": { sequence: 10, content: { title: "Early", render: () => null } } } },
    });
    expect(ids(resolveContainer(composeAddons([a, b], IDENTITY_CANONICALIZER).containers, "shell#drawers-bottom"))).toEqual([
      "b.early",
      "a.late",
    ]);
  });

  test("keeps the same drawer id separate under a different edge", () => {
    const logs = { content: { title: "Logs", render: () => null } };
    const a = defineAddon({
      id: "a",
      containers: { "shell#drawers-right": { "a.logs": logs }, "shell#drawers-bottom": { "a.logs": logs } },
    });
    const { containers } = composeAddons([a], IDENTITY_CANONICALIZER);
    expect(ids(resolveContainer(containers, "shell#drawers-right"))).toEqual(["a.logs"]);
    expect(ids(resolveContainer(containers, "shell#drawers-bottom"))).toEqual(["a.logs"]);
  });

  test("at most one installed addon provides the dashboard store", () => {
    const store = {} as NonNullable<Parameters<typeof defineAddon>[0]["dashboardStore"]>;
    expect(composeAddons([defineAddon({ id: "a", dashboardStore: store })], IDENTITY_CANONICALIZER).dashboards.store).toBe(store);
    expect(composeAddons([defineAddon({ id: "a" })], IDENTITY_CANONICALIZER).dashboards.store).toBeNull();
    expect(() => composeAddons([
      defineAddon({ id: "a", dashboardStore: store }),
      defineAddon({ id: "b", dashboardStore: store }),
    ], IDENTITY_CANONICALIZER)).toThrow(/"a", "b" each provide the dashboard store/);
  });

  test("rejects two addons that declare the same widget key", () => {
    const a = defineAddon({ id: "a", widgets: { text: "A" } });
    const b = defineAddon({ id: "b", widgets: { text: "B" } });
    expect(() => composeAddons([a, b], IDENTITY_CANONICALIZER)).toThrow(/text/);
  });

  test("merges data providers keyed by provider name", () => {
    const a = defineAddon({ id: "a", dataProviders: { operator: "OP" } });
    const b = defineAddon({ id: "b", dataProviders: { ledger: "LEDGER" } });
    expect(composeAddons([a, b], IDENTITY_CANONICALIZER).dataProviders).toEqual({
      operator: "OP",
      ledger: "LEDGER",
    });
  });

  test("orders layout providers and rejects a second claim on the same layout id", () => {
    const a = defineAddon({ id: "a", layoutProviders: [{ id: "connection", layout: "console", component: "A", sequence: 20 }] });
    const b = defineAddon({ id: "b", layoutProviders: [{ id: "session", layout: "console", component: "B", sequence: 10 }] });
    expect(composeAddons([a, b], IDENTITY_CANONICALIZER).layoutProviders.map((provider) => provider.id))
      .toEqual(["session", "connection"]);
    expect(() => composeAddons([a, { ...a, id: "duplicate" }], IDENTITY_CANONICALIZER))
      .toThrow(/layout provider/);
  });

  test("rejects two addons that claim the same data provider name", () => {
    const a = defineAddon({ id: "a", dataProviders: { operator: "A" } });
    const b = defineAddon({ id: "b", dataProviders: { operator: "B" } });
    expect(() => composeAddons([a, b], IDENTITY_CANONICALIZER)).toThrow(
      /data provider "operator"/,
    );
  });

  test("keeps equal-sequence provider nesting independent of addon order", () => {
    const a = defineAddon({ id: "a", layoutProviders: [{ id: "a", layout: "console", component: "A" }] });
    const b = defineAddon({ id: "b", layoutProviders: [{ id: "b", layout: "console", component: "B" }] });
    expect(composeAddons([b, a], IDENTITY_CANONICALIZER).layoutProviders)
      .toEqual(composeAddons([a, b], IDENTITY_CANONICALIZER).layoutProviders);
  });
});
