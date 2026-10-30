// @vitest-environment happy-dom

import { type ReactNode } from "react";
import { waitFor } from "@testing-library/react";
import { ChatterProvider } from "@angee/ui/communication/index";
import { useChatterPresentation } from "@angee/ui/communication/Chatter";
import { testDataResource } from "@angee/metadata/testing";
import { describe, expect, test } from "vitest";

import { createApp, type RefineLayoutChromeProps } from "./create-app";
import { resourcePageRoutes } from "./define-base-addon";
import { TEST_SCHEMAS } from "./testing";

function Chrome({ children }: RefineLayoutChromeProps): ReactNode {
  return <ChatterProvider>{children}</ChatterProvider>;
}

function Detail(): ReactNode {
  const { context, tabs } = useChatterPresentation();
  return <div>
    <span>{context.view.kind}:{context.view.sqid ?? "none"}</span>
    <span>{context.route?.modelLabel}</span>
    {tabs.map((tab) => <span key={tab.id}>{tab.id}</span>)}
  </div>;
}

describe("native detail record capabilities", () => {
  test.each([
    { name: "computed row", facts: { canonicalLabel: null }, record: false },
    { name: "persisted read-only row", facts: { canonicalLabel: "catalog.Entry" }, record: true },
    { name: "omitted optional metadata", facts: {}, record: true },
  ])("$name keeps its model detail and only eligible record tabs", async ({ facts, record }) => {
    const host = document.createElement("div");
    document.body.append(host);
    history.replaceState(null, "", "/entries/selected");
    const resource = testDataResource("catalog.Entry", {
      ...facts,
      resourceType: null,
      roots: { list: "entries", detail: "entries_by_pk", create: null, update: null, delete: null },
      capabilities: ["list", "detail"],
    });
    const app = createApp({
      addons: [{
        id: "catalog",
        routes: resourcePageRoutes("entries", "/entries", () => null, "catalog.Entry", {
          detailComponent: Detail,
        }),
        containers: {
          "record#aside": {
            "catalog.record": { content: { label: "Record activity", render: () => null } },
          },
          "catalog.Entry#aside": {
            "catalog.definition": { content: { label: "Definition", render: () => null } },
          },
        },
      }],
      layouts: { console: { chrome: Chrome, requireAuth: false } },
      schemas: {
        ...TEST_SCHEMAS,
        console: {
          ...TEST_SCHEMAS.console,
          metadata: { angee: { resources: [resource] } },
        },
      },
      defaultSchema: "console",
      location: { search: "" },
    });
    const root = app.mount(host);
    try {
      await waitFor(() => expect(host.textContent).toContain(record ? "record:selected" : "dashboard:none"));
      expect(host.textContent).toContain("catalog.Entry");
      expect(host.textContent).toContain("catalog.definition");
      expect(host.textContent?.includes("catalog.record")).toBe(record);
    } finally {
      root.unmount();
      host.remove();
    }
  });
});
