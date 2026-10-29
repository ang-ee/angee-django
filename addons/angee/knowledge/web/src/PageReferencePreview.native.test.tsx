// @vitest-environment happy-dom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { composeAddons } from "@angee/app";
import { ShellPageTestProviders } from "@angee/app/testing";
import { createRouteHref, defaultWidgets, RecordReference, RecordReferencePreview } from "@angee/ui";
import { createUiTestProviders } from "@angee/ui/testing";
import { testDataResource } from "@angee/metadata/testing";

import knowledge from "./index";

const { Provider, clearClients } = createUiTestProviders({ resources: [testDataResource("knowledge.Page")] });
afterEach(() => { cleanup(); clearClients(); });

test("Knowledge contributes the title and first prose preview while retaining exact navigation", async () => {
  const getOne = vi.fn(async () => ({ data: {
    id: "pg_policy", title: "Posting policy",
    markdown: { body: "# Internal heading\nBills require approval before posting.\n\nLater paragraph." },
  } }));
  render(<Provider dataProvider={{ getOne }}><ShellPageTestProviders runtime={{
    widgets: defaultWidgets,
    routeHref: createRouteHref([{ name: "knowledge.page", path: "/knowledge/$id" }]),
    routesByResource: { "knowledge.Page": { collection: "knowledge", record: { name: "knowledge.page", param: "id" } } },
    containers: composeAddons([knowledge], { canonicalModelLabel: (model) => model }).containers,
  }}>
    <RecordReferencePreview model="knowledge.Page" id="pg_policy" label="Retained label" tab="reader" search={{ passage: "approval" }} />
    <RecordReference model="knowledge.Page" id="pg_inline" label="Inline link" />
  </ShellPageTestProviders></Provider>);
  expect((await screen.findByRole("link", { name: "Posting policy" })).getAttribute("href"))
    .toBe("/knowledge/pg_policy?recordTab=reader&passage=approval");
  expect(await screen.findByText("Bills require approval before posting.")).toBeTruthy();
  expect(screen.queryByText("Internal heading")).toBeNull();
  expect(screen.queryByText("Later paragraph.")).toBeNull();
  expect(screen.getByRole("link", { name: "Inline link" })).toBeTruthy();
  expect(getOne).toHaveBeenCalledTimes(1);
});
