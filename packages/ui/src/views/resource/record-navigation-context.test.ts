import { expect, test } from "vitest";
import { testDataResource } from "@angee/metadata/testing";
import { parseRecordNavigationScope, recordNavigationHref, recordNavigationSearch } from "./record-navigation-context";
const resource = testDataResource("notes.Note");
import type { ListViewNavigationScope } from "./resource-view-surface";
const scope: ListViewNavigationScope = { filter: { AND: [{ title: { iContains: "draft" } }, { updated_at: { gte: "2026-09-01", lt: "2026-10-01" } }] }, order: { updated_at: "DESC" }, page: 2, pageSize: 20 };

test("portable context keeps parent query and clicked leaf facts, without record payloads", () => {
  const parent = { group: "updated_at:month", filter: '{"title":{"iContains":"draft"}}', page: "3", keep: "external" };
  const nav = recordNavigationSearch(parent, resource, { ...scope, rows: [{ id: "secret", body: "PRIVATE" }], total: 99 } as typeof scope);
  expect(nav).toMatchObject(parent);
  expect(JSON.stringify(nav)).not.toContain("PRIVATE");
  expect(JSON.stringify(nav)).not.toContain("secret");
  expect(parseRecordNavigationScope(nav, resource)).toEqual(scope);
  expect(recordNavigationSearch(nav, resource, null)).toEqual(parent);
  const href = recordNavigationHref("/notes/note-2?group=updated_at%3Amonth&page=3&keep=external#body", resource, scope);
  const url = new URL(href, "https://test.invalid");
  expect(url.hash).toBe("#body");
  expect(url.searchParams.get("page")).toBe("3");
  expect(parseRecordNavigationScope(Object.fromEntries(url.searchParams), resource)).toEqual(scope);
});

test.each([
  { ...scope, page: 0 }, { ...scope, pageSize: 101 }, { ...scope, page: Number.MAX_SAFE_INTEGER + 1 },
  { ...scope, filter: [] }, { ...scope, filter: { NOT: { status: { exact: "hidden" } } } },
  { ...scope, filter: { title: { mystery: "ignored" } } }, { ...scope, order: { updated_at: "sideways" } },
  { ...scope, order: { author: { name: "ASC" } } }, { ...scope, rows: [{ id: "hidden" }] },
])("rejects invalid or unsupported complete context: %j", (invalid) => {
  const search = { recordNav: JSON.stringify({ version: 1, model: resource.modelLabel, schema: resource.schemaName, scope: invalid }) };
  expect(parseRecordNavigationScope(search, resource)).toBeNull();
});

test("valid native dotted relation ordering round-trips and resource/provider mismatches fail closed", () => {
  const search = recordNavigationSearch({}, resource, { ...scope, order: { "author.display_name": "ASC" } });
  expect(parseRecordNavigationScope(search, resource)?.order).toEqual({ "author.display_name": "ASC" });
  expect(parseRecordNavigationScope(search, testDataResource("storage.File"))).toBeNull();
  expect(parseRecordNavigationScope(search, { ...resource, schemaName: "public" })).toBeNull();
  expect(parseRecordNavigationScope(search, undefined)).toBeNull();
  expect(parseRecordNavigationScope({ recordNav: "{" }, resource)).toBeNull();
  expect(parseRecordNavigationScope({ recordNav: "x".repeat(8193) }, resource)).toBeNull();
});

test("client row models cannot be made server-pageable by a portable descriptor", () => {
  const clientResource = { ...resource, rowModel: "client" as const };
  const serverContext = recordNavigationSearch({}, resource, scope);
  expect(parseRecordNavigationScope(serverContext, clientResource)).toBeNull();
  expect(recordNavigationSearch({}, clientResource, scope)).toEqual({});
});
