import { describe, expect, test } from "vitest";

import { UnknownRouteError, createRouteHref } from "./route-href";

const routeHref = createRouteHref([
  { name: "notes.list", path: "/notes" },
  { name: "notes.record", path: "/notes/$id" },
  { name: "parties.merge", path: "/parties/merge/$left/$right" },
]);

describe("routeHref", () => {
  test("resolves projected record links with the declared param and search", () => {
    const href = createRouteHref([
      { name: "desk.notes", path: "/desk/notes" },
      { name: "desk.note", path: "/desk/notes/$noteId" },
    ], { "notes.Note": { collection: "desk.notes", record: { name: "desk.note", param: "noteId" } } });
    expect(href.record("notes.Note", "a/b", { tab: "files" })).toBe("/desk/notes/a%2Fb?tab=files");
    expect(() => href.record("missing.Model", "1")).toThrow(/no record route/);
  });
  test("resolves a collection route", () => {
    expect(routeHref("notes.list")).toBe("/notes");
  });

  test("interpolates a one-param route", () => {
    expect(routeHref("notes.record", { id: "note-1" })).toBe(
      "/notes/note-1",
    );
  });

  test("interpolates a two-param route", () => {
    expect(routeHref("parties.merge", { left: "a", right: "b" })).toBe(
      "/parties/merge/a/b",
    );
  });

  test("fails fast for an unknown route name", () => {
    expect(() => routeHref("missing.route")).toThrow(UnknownRouteError);
    expect(() => routeHref("missing.route")).toThrowError(
      'Unknown route name "missing.route".',
    );
  });

  test("probes an unknown route without throwing", () => {
    expect(routeHref.maybe("notes.list")).toBe("/notes");
    expect(routeHref.maybe("missing.route")).toBeUndefined();
    expect(() => routeHref.maybe("notes.record")).toThrow(
      /missing params: id/,
    );
  });

  test("fails fast for missing and extra params", () => {
    expect(() => routeHref("notes.record")).toThrowError(
      'Route "notes.record" is missing params: id.',
    );
    expect(() => routeHref("notes.list", { id: "extra" })).toThrowError(
      'Route "notes.list" received extra params: id.',
    );
  });

  test("URL-encodes interpolated params", () => {
    expect(routeHref("notes.record", { id: "folder/a b" })).toBe(
      "/notes/folder%2Fa%20b",
    );
  });

  test("preserves requested search", () => {
    expect(routeHref("notes.record", { id: "a" }, "?view=board&group=kind"))
      .toBe("/notes/a?view=board&group=kind");
    expect(routeHref("notes.list", undefined, { model: "notes.Note", page: 2 }))
      .toBe("/notes?model=notes.Note&page=2");
    expect(routeHref("notes.list", undefined, { filter: "", empty: "", absent: undefined }))
      .toBe("/notes?filter=&empty=");
  });

  test.each(["/files/$", "/files/{-$id}", "/files/prefix{$id}"])(
    "fails fast on unsupported template syntax in %s",
    (path) => {
      expect(() => createRouteHref([{ name: "files.bad", path }])).toThrow(
        /unsupported template segment/,
      );
    },
  );
});
