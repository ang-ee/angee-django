import { describe, expect, test, vi } from "vitest";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  Field,
  REFINE_CREATE_ID,
  pageElementProps,
  parsePageActions,
  parsePageFields,
  type FormProps,
} from "@angee/ui";

vi.mock("./i18n", () => ({ usePartiesT: () => (key: string) => key }));
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useActionOutcomeMutation: vi.fn(() => [vi.fn(), { fetching: false, error: null }]),
    useSlot: vi.fn(() => []),
  };
});
import { useSlot } from "@angee/ui";
import { OrganizationForm } from "./OrganizationsPage";
import { PersonForm } from "./PersonForm";
import { ORGANIZATION_FORM_FIELDS_SLOT } from "./slots";

function renderOrganizationForm(props: Parameters<typeof OrganizationForm>[0]) {
  let formView: ReturnType<typeof OrganizationForm> | undefined;
  function Probe() {
    formView = OrganizationForm(props);
    return null;
  }
  renderToStaticMarkup(<Probe />);
  if (!formView) throw new Error("Expected the organization form to render");
  return formView;
}

function formFields() {
  const formView = renderOrganizationForm({ resource: "parties.Organization", id: null });
  const form = pageElementProps<FormProps>(formView, "form");
  if (!form) throw new Error("Expected the native organization form");
  return parsePageFields(form.children).map((field) => field.name);
}

function recordTabIds() {
  const formView = renderOrganizationForm({ resource: "parties.Organization", id: "party-1" });
  const form = pageElementProps<FormProps>(formView, "form");
  if (!form) throw new Error("Expected the native organization form");
  return (form.recordTabs ?? []).map((tab) => tab.id);
}

function renderPersonForm(props: Parameters<typeof PersonForm>[0]) {
  let formView: ReturnType<typeof PersonForm> | undefined;
  function Probe() {
    formView = PersonForm(props);
    return null;
  }
  renderToStaticMarkup(<Probe />);
  if (!formView) throw new Error("Expected the person form to render");
  return formView;
}

function nativeForm(view: ReactElement): FormProps {
  const form = pageElementProps<FormProps>(view, "form");
  if (!form) throw new Error("Expected a native form");
  return form;
}

describe("organization form extensions", () => {
  test("shares saved contact actions with person forms and preserves native form gates", () => {
    vi.mocked(useSlot).mockReturnValue([]);
    const forms = [
      nativeForm(renderOrganizationForm({
        resource: "parties.Organization",
        id: "party-1",
        readOnly: true,
      })),
      nativeForm(renderPersonForm({
        resource: "parties.Person",
        id: "party-2",
        readOnly: true,
      })),
    ];

    for (const form of forms) {
      expect(form.readOnly).toBe(true);
      const actions = parsePageActions(form.children);
      expect(actions.map((action) => action.id)).toEqual(["add-email", "add-phone"]);
      for (const action of actions) {
        expect(action.visibleWhen?.({ id: REFINE_CREATE_ID })).toBe(false);
        expect(action.visibleWhen?.({ id: "party-saved" })).toBe(true);
      }
    }
  });

  test("shares canonical identity and address tabs with person records", () => {
    expect(recordTabIds()).toEqual(["identity", "addresses"]);
  });
  test("keeps the base contact fields independent of consumers", () => {
    vi.mocked(useSlot).mockReturnValue([]);
    expect(formFields()).toEqual(["display_name", "legal_name", "domain", "notes"]);
  });
  test("includes consumer field declarations after the organization identity", () => {
    vi.mocked(useSlot).mockReturnValue([
      { slot: ORGANIZATION_FORM_FIELDS_SLOT, id: "consumer.reference", content: <Field name="external_reference" /> },
    ]);
    expect(formFields()).toEqual(["display_name", "legal_name", "domain", "external_reference", "notes"]);
    expect(useSlot).toHaveBeenCalledWith(ORGANIZATION_FORM_FIELDS_SLOT);
  });
});
