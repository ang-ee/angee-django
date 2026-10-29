import { expect, test } from "vitest";
import { Filter } from "@angee/metadata";
import { pageCollectionFilter } from "./page-source";

const request = { filter: {}, order: {}, page: 1, pageSize: 50 };
test("an unfiltered tree requests visible roots, while search requests matches at every depth", () => {
  expect(Filter.from(pageCollectionFilter("vault", request)).value).toEqual({
    vault: { exact: "vault" }, is_trashed: { exact: false }, navigation_root: { exact: true },
  });
  expect(Filter.from(pageCollectionFilter("vault", { ...request, filter: { title: { iContains: "child" } } })).value)
    .toEqual({ vault: { exact: "vault" }, is_trashed: { exact: false }, title: { iContains: "child" } });
});
test("child paging keeps the mounted scope and parent alongside the live-page predicate", () => {
  const filter = pageCollectionFilter("vault", { ...request, parentId: "folder" }, { title: { iContains: "Guide" } });
  expect(Filter.from(filter).value).toEqual({ vault: { exact: "vault" }, is_trashed: { exact: false },
    title: { iContains: "Guide" }, parent: { exact: "folder" } });
});
