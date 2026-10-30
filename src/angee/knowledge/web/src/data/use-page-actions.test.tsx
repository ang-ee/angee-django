// @vitest-environment happy-dom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "@angee/ui/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import { testDataResource } from "@angee/metadata/testing";
import { KnowledgeCreatePage, PAGE_MODEL } from "./documents";
import { usePageActions } from "./use-page-actions";

const { Provider, clearClients } = createUiTestProviders({ resources: [testDataResource(PAGE_MODEL)] });
afterEach(() => { cleanup(); clearClients(); });

test("native mutations preserve the created identity and use the resource hierarchy update", async () => {
  const custom = vi.fn<NonNullable<RefineTestDataProvider["custom"]>>(async () => ({ data: { create_page: { id: "pg_new" } } }));
  const update = vi.fn<NonNullable<RefineTestDataProvider["update"]>>(async () => ({ data: { id: "pg_new" } }));
  const { result, rerender } = renderHook(usePageActions, {
    wrapper: ({ children }) => <Provider dataProvider={{ custom, update }}>{children}</Provider>,
  });
  const first = result.current;
  rerender();
  expect(result.current.createPage).toBe(first.createPage);
  expect(result.current.movePage).toBe(first.movePage);
  const input = { vault: "vlt_1", title: "Child", kind: "note", parent: "pg_parent" };
  await act(async () => {
    expect(await result.current.createPage(input)).toBe("pg_new");
    await result.current.movePage("pg_new", "pg_other");
  });
  expect(custom).toHaveBeenCalledWith(expect.objectContaining({ meta: expect.objectContaining({ gqlMutation: KnowledgeCreatePage, gqlVariables: input }) }));
  expect(update).toHaveBeenCalledWith(expect.objectContaining({ id: "pg_new", variables: { parent: "pg_other" } }));
});
