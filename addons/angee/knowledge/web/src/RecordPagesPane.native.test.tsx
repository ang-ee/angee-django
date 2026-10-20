// @vitest-environment happy-dom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useCallback, useState } from "react";
import { createUiTestProviders } from "@angee/ui/testing";
import {
  AppRuntimeProvider, CHATTER_CONTAINERS, RelatedPane, RelatedCountProbe, containersFromChildren, createRouteHref, type ChatterViewContext,
} from "@angee/ui";

import { KnowledgeRecordPages } from "./data/documents";
import { recordPagesGroup } from "./RecordPagesPane";

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://record-pages" });
afterEach(() => { cleanup(); clearClients(); });

const context: ChatterViewContext = {
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: { name: "record", path: "/records/$id", viewType: "example/record", modelLabel: "example.Record", recordEdges: ["knowledge.RecordBinding"] },
  view: { kind: "record", type: "example/record", sqid: "rec_1" },
};

function Count() {
  const [count, setCount] = useState<number>();
  const publish = useCallback((_: string, next: number | undefined) => setCount(next), []);
  return <><RelatedCountProbe id="related" context={context} onCount={publish} /><span>Related {count}</span></>;
}

test("generic and role groups share one unfiltered request across counts, presence and rows", async () => {
  const custom = vi.fn(async () => ({ data: {
    record_knowledge_can_bind: false,
    record_knowledge_bindings: [
      { id: "b1", page: "contract", page_title: "Signed contract", page_can_write: false, role: "contract" },
      { id: "b2", page: "related", page_title: "Notes", page_can_write: false, role: "related" },
      { id: "b3", page: "reference", page_title: "Unclaimed reference", page_can_write: false, role: "reference" },
    ],
  } }));
  render(<Provider dataProvider={{ custom }}><AppRuntimeProvider runtime={{
    routeHref: createRouteHref([{ name: "knowledge.page", path: "/knowledge/$id" }]),
    containers: containersFromChildren(CHATTER_CONTAINERS, {
      "related#groups": { pages: recordPagesGroup() },
      "example.Record#groups": { contract: recordPagesGroup({ label: "Contract", role: "contract" }) },
    }),
  }}><Count /><RelatedPane context={context} /></AppRuntimeProvider></Provider>);
  await screen.findByText("Related 3");
  await waitFor(() => expect(screen.getAllByRole("link")).toHaveLength(3));
  expect(screen.getAllByRole("link", { name: "Signed contract" })).toHaveLength(1);
  expect(screen.getByText("Pages").closest("li")?.textContent).toBe("Pages· 2");
  expect(screen.getByText("Contract").closest("li")?.textContent).toBe("Contract· 1");
  expect(custom).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ meta: expect.objectContaining({
    gqlQuery: KnowledgeRecordPages, gqlVariables: { modelLabel: "example.Record", recordId: "rec_1", role: null },
  }) }));
});
