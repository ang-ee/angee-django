// @vitest-environment happy-dom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { tanStackRouterProvider } from "@angee/refine";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { afterEach, expect, test } from "vitest";
import { Breadcrumb, BreadcrumbLabelProvider } from "../../chrome/Breadcrumb";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider, createAngeeI18nInstance } from "../../runtime";
import { createUiTestProviders } from "../../testing";
import { defaultWidgets } from "../../widgets";
import { FormView } from "./FormView";

const resource = testDataResource("notes.Note", {
  recordRepresentation: "title", fields: [{ name: "title", kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: true, updatable: true, requiredOnCreate: true }],
  query: testResourceQuery({ fields: { title: testQueryField("title") } }),
});
const ui = createUiTestProviders({ routerProvider: tanStackRouterProvider, resources: [resource],
  refineResources: [{ name: "notes", list: "/notes", show: "/notes/:id", meta: { label: "Notes", dataProviderName: "console" } }],
});
afterEach(() => { cleanup(); ui.clearClients(); });

test.each(["Create", "Créer"])("a /new form publishes the localized %s breadcrumb", async (label) => {
  const root = createRootRoute({ component: () => <ui.Provider><AppRuntimeProvider runtime={{ widgets: defaultWidgets,
    i18n: createAngeeI18nInstance({ ui: { "form.create": label } }) }}>
    <ModalsHost><ToastProvider><BreadcrumbLabelProvider>
      <Breadcrumb /><FormView resource="notes.Note" id={null} fields={[{ name: "title" }]} publishBreadcrumbLabel />
    </BreadcrumbLabelProvider></ToastProvider></ModalsHost>
  </AppRuntimeProvider></ui.Provider> });
  const router = createRouter({ routeTree: root.addChildren([createRoute({ getParentRoute: () => root, path: "/notes/$id" })]),
    history: createMemoryHistory({ initialEntries: ["/notes/new"] }),
  });
  render(<RouterProvider router={router} />);
  const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
  await waitFor(() => expect(breadcrumb.textContent).toBe(`Notes/${label}`));
  expect(breadcrumb.textContent).not.toContain("Show");
});
