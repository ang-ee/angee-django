import type { ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";
import { Field, pageChildren, pageElementProps, parsePageFields, type FormProps } from "@angee/ui";

vi.mock("./i18n", () => ({ usePartiesT: () => (key: string) => key }));
vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return { ...actual, useSlot: vi.fn(() => []) };
});
import { useSlot } from "@angee/ui";
import { OrganizationsPage } from "./OrganizationsPage";
import { ORGANIZATION_FORM_FIELDS_SLOT } from "./slots";

function formFields() {
  const page = OrganizationsPage();
  const children = (page.props as { children?: ReactNode }).children;
  const form = pageChildren(children)
    .map((child) => pageElementProps<FormProps>(child, "form"))
    .find((props): props is FormProps => Boolean(props));
  if (!form) throw new Error("Expected the native organization form");
  return parsePageFields(form.children).map((field) => field.name);
}

describe("organization form extensions", () => {
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
