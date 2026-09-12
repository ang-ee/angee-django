// @vitest-environment happy-dom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { Refine, useCreate, type DataProvider } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import {
  refineResourceName,
  refineResourcesFromDataResources,
} from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { ToastProvider, useRefineNotificationProvider } from "@angee/ui/feedback/index";
import { enUiMessages } from "@angee/ui/i18n";
import { afterEach, expect, test, vi } from "vitest";

import { createAngeeI18nRuntime } from "../providers/i18n";
import { resourceLabelI18nMessages } from "../resource-projection";

afterEach(() => cleanup());

// The toast an investor sees after "Create project". Everything between the
// mutation and the rendered string is real: refine's own `useCreate` composes
// the message, the app's i18n provider resolves it, and the app's notification
// provider renders it.
function fixture(modelLabel = "projects.Project", modelName = "project") {
  // `modelName` comes from Django's `model._meta.model_name`, which is lower
  // case ("task", "project"). A fixture that capitalizes it tests a resource
  // shape that cannot occur.
  const resource = testDataResource(modelLabel, { modelName });
  const schemas = {
    console: { metadata: { angee: { resources: [resource] } } },
  };
  const i18n = createAngeeI18nRuntime({
    ui: { ...enUiMessages, ...resourceLabelI18nMessages(schemas).ui },
  });
  const dataProvider = {
    create: vi.fn(async () => ({ data: { id: "1" } })),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    getOne: vi.fn(async () => ({ data: { id: "1" } })),
    update: vi.fn(async () => ({ data: { id: "1" } })),
    deleteOne: vi.fn(async () => ({ data: { id: "1" } })),
    getApiUrl: () => "https://fixture.invalid",
  } as unknown as Required<DataProvider>;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  let create!: ReturnType<typeof useCreate>;
  function Probe() {
    create = useCreate();
    return null;
  }
  function App() {
    return (
      <Refine
        dataProvider={{ default: dataProvider, console: dataProvider }}
        i18nProvider={i18n.provider}
        notificationProvider={useRefineNotificationProvider()}
        resources={[...refineResourcesFromDataResources([resource])]}
        options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}
      >
        <Probe />
      </Refine>
    );
  }
  render(
    <ToastProvider>
      <App />
    </ToastProvider>,
  );
  return { resource, create: () => create, client };
}

test("names the model in the create toast instead of the resource identifier", async () => {
  const f = fixture();

  await act(async () => {
    // The list root is what the form save passes; refine resolves it to the
    // registered resource, whose identifier is `console:projects.Project` --
    // the string that used to reach the toast.
    f.create().mutate({
      resource: refineResourceName(f.resource),
      values: { name: "Signage refresh" },
    });
  });

  await waitFor(() => expect(screen.getByText("Project created")).toBeTruthy());
  // The label stays lower case, because that is its case inside a sentence
  // ("Could not create project"); only the sentence is capitalized.
  expect(screen.queryByText("project created")).toBeNull();

  // The two strings the demo script caught: refine's English default naming the
  // resource by its raw identifier, and its untranslated description key.
  expect(screen.queryByText(/console:projects\.Project/)).toBeNull();
  expect(screen.queryByText("notifications.success")).toBeNull();
  expect(screen.queryByText(/Successfully created/)).toBeNull();

  // One line, not two: refine pairs a generic description with every success
  // toast, and an empty second line is worse than none.
  expect(document.body.textContent).toBe("Project created");

  f.client.clear();
});

test("capitalizes the sentence on the board path too", async () => {
  // The string the reviewer saw creating a task from the queue board: the label
  // is the model's own lower-case name, so the sentence started lower case.
  const f = fixture("projects.Task", "task");

  await act(async () => {
    f.create().mutate({
      resource: refineResourceName(f.resource),
      values: { title: "Draft signage" },
    });
  });

  await waitFor(() => expect(screen.getByText("Task created")).toBeTruthy());
  expect(screen.queryByText("task created")).toBeNull();

  f.client.clear();
});
