// @vitest-environment happy-dom

import { createUiTestProviders } from "@angee/ui/testing";
import type { RefineTestDataProvider } from "@angee/refine/testing";
import type { ComponentProps, ReactElement } from "react";
import { testDataResource } from "@angee/metadata/testing";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, Field, ModalsHost, ToastProvider, baseIcons, defaultWidgets } from "@angee/ui";
import { OrganizationForm } from "./OrganizationsPage";
import { PersonForm } from "./PersonForm";
import { ORGANIZATION_FORM_FIELDS_SLOT } from "./slots";

vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, {
    useActionOutcomeMutation: vi.fn(() => [vi.fn(), { fetching: false, error: null }]),
  });
});

const forms = [
  { resource: "parties.Organization", Component: OrganizationForm },
  { resource: "parties.Person", Component: PersonForm },
];
const resources = forms.map(({ resource }) => testDataResource(resource, {
  recordRepresentation: "display_name",
  fields: [
    "id", "display_name", "legal_name", "domain", "notes", "external_reference",
    "given_name", "family_name", "additional_name", "nickname", "name_prefix",
    "name_suffix", "birthday", "anniversary", "folder",
  ].map((name) => ({
    name, kind: "scalar" as const, scalar: "String", readable: true,
    filterable: false, sortable: false, aggregatable: false, groupable: false,
    creatable: true, updatable: true, requiredOnCreate: false,
  })),
}));

const { Provider, clearClients } = createUiTestProviders({ apiUrl: "test://parties" });
afterEach(() => {
  cleanup();
  clearClients();
});

function renderPartyForm(
  form: ReactElement,
  slots: ComponentProps<typeof AppRuntimeProvider>["runtime"]["slots"] = [],
) {
  const provider = {
    getOne: vi.fn(async () => ({ data: { id: "party-1", display_name: "Saved party" } })),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
  } satisfies RefineTestDataProvider;
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  return render(
    <Provider resources={resources} dataProvider={provider} queryClientConfig={{ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }}>
      <RouterContextProvider router={router}>
        <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets, icons: baseIcons, slots }}>
          {form}
        </AppRuntimeProvider></ToastProvider></ModalsHost>
      </RouterContextProvider>
    </Provider>,
  );
}

describe("organization form extensions", () => {
  test.each(forms)("offers contact actions for saved $resource records", async ({ resource, Component }) => {
    renderPartyForm(<Component resource={resource} id="party-1" />);
    await screen.findByDisplayValue("Saved party");
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    expect(await screen.findByRole("menuitem", { name: "Add email" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Add phone" })).toBeTruthy();
  });

  test.each(forms)("hides contact actions while creating $resource records", ({ resource, Component }) => {
    renderPartyForm(<Component resource={resource} id={null} />);
    expect(screen.getByRole("button", { name: "Create" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  });

  test.each(forms)("retains the native read-only gate for $resource records", async ({ resource, Component }) => {
    renderPartyForm(<Component resource={resource} id="party-1" readOnly />);
    expect(await screen.findByRole("heading", { name: "Saved party" })).toBeTruthy();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
  });

  test("offers identity and address tabs on saved organizations", async () => {
    renderPartyForm(<OrganizationForm resource="parties.Organization" id="party-1" />);
    await screen.findByDisplayValue("Saved party");
    expect(screen.getByRole("tab", { name: "Identity" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Addresses" })).toBeTruthy();
  });

  test.each([false, true])("keeps base fields with consumer extension present: %s", (withExtension) => {
    renderPartyForm(<OrganizationForm resource="parties.Organization" id={null} />, withExtension ? [
      { slot: ORGANIZATION_FORM_FIELDS_SLOT, id: "consumer.reference", content: <Field name="external_reference" label="External reference" /> },
    ] : []);
    const title = screen.getByRole("textbox", { name: /display name/i });
    expect(screen.getByRole("textbox", { name: "Legal name" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Domain" })).toBeTruthy();
    const notes = screen.getByRole("textbox", { name: /notes/i });
    const extension = screen.queryByRole("textbox", { name: "External reference" });
    if (withExtension) {
      expect(extension).toBeTruthy();
      expect(title.compareDocumentPosition(extension!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      expect(extension!.compareDocumentPosition(notes)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    } else {
      expect(extension).toBeNull();
    }
  });
});
