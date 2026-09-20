// @vitest-environment happy-dom

import type { ComponentProps, ReactElement } from "react";
import { ModelMetadataProvider, refineResourcesFromDataResources, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { Refine, type DataProvider } from "@angee/refine";
import { QueryClient } from "@tanstack/react-query";
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
const clients: QueryClient[] = [];

afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
});

function renderPartyForm(
  form: ReactElement,
  slots: ComponentProps<typeof AppRuntimeProvider>["runtime"]["slots"] = [],
) {
  const provider = {
    getApiUrl: () => "test://parties",
    getOne: vi.fn(async () => ({ data: { id: "party-1", display_name: "Saved party" } })),
    getList: vi.fn(async () => ({ data: [], total: 0 })),
    create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  clients.push(client);
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  return render(
    <Refine resources={[...refineResourcesFromDataResources(resources)]} dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
      <RouterContextProvider router={router}>
        <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources(resources)}>
          <ModalsHost><ToastProvider><AppRuntimeProvider runtime={{ widgets: defaultWidgets, icons: baseIcons, slots }}>
            {form}
          </AppRuntimeProvider></ToastProvider></ModalsHost>
        </ModelMetadataProvider>
      </RouterContextProvider>
    </Refine>,
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
