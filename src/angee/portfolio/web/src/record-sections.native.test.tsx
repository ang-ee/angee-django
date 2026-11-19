// @vitest-environment happy-dom
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { RecordChromeProvider } from "@angee/ui";
import { RuntimeFixture, storySchema, testDataResource, type GraphQLWireRequest } from "@angee/storybook/testing";

import { PromoteProjectAction } from "./record-sections";

function renderPromotion({ record, readOnly = false }: { record: Record<string, unknown> | null; readOnly?: boolean }) {
  const requests: GraphQLWireRequest[] = [];
  const fixture = storySchema({
    promote_project_to_product: () => ({ ok: true, message: "Project promoted.", id: "pdt_1", code: null, validation_errors: null }),
  }, (request) => requests.push(request)).public!;
  const schemas = { console: { ...fixture, metadata: { angee: { resources: [
    testDataResource("projects.Project", { roots: { list: "projects", detail: "projects_by_pk" } }),
    testDataResource("portfolio.Product", { roots: { list: "portfolio_products", detail: "portfolio_products_by_pk" } }),
  ] } } } };
  render(<RuntimeFixture activeSchema="console" schemas={schemas}>
    <RecordChromeProvider value={{ resource: "projects.Project", canonicalResource: "projects.Project", dataProviderName: "console",
      recordId: record ? "prj_1" : "", record, formReadOnly: readOnly }}>
      <PromoteProjectAction />
    </RecordChromeProvider>
  </RuntimeFixture>);
  return requests;
}

test.each(["OPEN", "PAUSED", "DONE", "DROPPED"])("a writable %s project's menu promotes the open record", async (status) => {
  const requests = renderPromotion({ record: { id: "prj_1", status, permissions: ["write"] } });
  expect(screen.queryByRole("button", { name: "Promote to product" })).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "Actions" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Promote to product" }));
  expect(await screen.findByText("Project promoted.")).toBeTruthy();
  expect(requests.find(({ fields }) => fields.includes("promote_project_to_product"))?.variables).toEqual({ id: "prj_1" });
});

test.each([
  { record: null },
  { record: { id: "prj_1", permissions: [] } },
  { record: { id: "prj_1", permissions: ["write"] }, readOnly: true },
])("promotion is absent without a saved writable record: %j", (options) => {
  renderPromotion(options);
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: "Promote to product" })).toBeNull();
});
