// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { TypedDocumentNode } from "@angee/refine";
import type { ChatterViewContext } from "../runtime";

const query = vi.hoisted(() => vi.fn());
vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()), useAuthoredQuery: query,
}));

import { relatedCount } from "./related-count";

const document = { kind: "Document", definitions: [] } as unknown as TypedDocumentNode<{
  edges_aggregate: { aggregate: { count: number } | null };
}, { model: string; id: string }>;
const context: ChatterViewContext = {
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: { name: "record", path: "/records/$id", viewType: "record", modelLabel: "example.Record" },
  view: { kind: "record", type: "record", sqid: "rec_1" },
};

test("relatedCount projects an authored aggregate with the declared live interests and loading semantics", () => {
  const useCount = relatedCount(document, (current) => ({
    variables: { model: current.route?.modelLabel ?? "", id: current.view.kind === "record" ? current.view.sqid ?? "" : "" },
    options: { models: ["example.Edge"], records: [{ model: "example.Record", id: "rec_1" }] },
  }), (data) => data.edges_aggregate.aggregate?.count);
  query.mockReturnValue({ data: undefined });
  const view = renderHook(() => useCount(context));
  expect(view.result.current).toBeUndefined();
  expect(query).toHaveBeenCalledWith(document, { model: "example.Record", id: "rec_1" }, {
    models: ["example.Edge"], records: [{ model: "example.Record", id: "rec_1" }],
  });
  query.mockReturnValue({ data: { edges_aggregate: { aggregate: { count: 3 } } } });
  view.rerender();
  expect(view.result.current).toBe(3);
});
