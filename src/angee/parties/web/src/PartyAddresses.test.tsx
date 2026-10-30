// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { pageChildren, pageElementProps, parsePageFields, type DrawerResourceListProps, type FormProps, type RecordPanelContext } from "@angee/ui";

import { PartyAddresses } from "./PartyAddresses";

const capture = vi.hoisted(() => ({ props: null as DrawerResourceListProps | null }));

const formSurface = (formReadOnly: boolean) => ({ formReadOnly }) as RecordPanelContext["form"];

vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  DrawerResourceList: (props: DrawerResourceListProps) => {
    capture.props = props;
    return null;
  },
}));

describe("PartyAddresses", () => {
  test("owns a scoped create/edit form with the complete postal address", () => {
    render(<PartyAddresses recordId="party_7" form={formSurface(false)} />);
    const props = capture.props;
    expect(props).toMatchObject({
      resource: "parties.Address",
      presentation: "embedded",
      baseFilter: { party: { exact: "party_7" } },
      createDefaults: { party: "party_7" },
    });
    const form = pageChildren(props?.children)
      .map((child) => pageElementProps<FormProps>(child, "form"))
      .find((candidate): candidate is FormProps => Boolean(candidate));
    expect(parsePageFields(form?.children)).toMatchObject([
      { name: "party", readOnly: true },
      { name: "label" },
      { name: "street", body: false },
      { name: "extended", body: false },
      { name: "po_box" },
      { name: "city" },
      { name: "region" },
      { name: "postal_code" },
      { name: "country" },
      { name: "is_primary", widget: "switch" },
    ]);
  });

  test("inherits a parent record peek's read-only state", () => {
    render(<PartyAddresses recordId="party_7" form={formSurface(true)} />);

    expect(capture.props?.hideCreate).toBe(true);
    const addressForm = pageChildren(capture.props?.children)
      .map((child) => pageElementProps<FormProps>(child, "form"))
      .find((candidate): candidate is FormProps => Boolean(candidate));
    expect(addressForm?.readOnly).toBe(true);
  });
});
