import { describe, expect, test } from "vitest";
import { pageChildren, pageElementProps, parsePageFields, type FormProps, type ListProps } from "@angee/ui";

import type { ReactNode } from "react";
import { PartyAddresses } from "./PartyAddresses";

describe("PartyAddresses", () => {
  test("owns a scoped create/edit form with the complete postal address", () => {
    const view = PartyAddresses({ recordId: "party_7", emptyContent: "No addresses yet." });
    const props = view.props as { children?: ReactNode; [key: string]: unknown };
    expect(props).toMatchObject({
      resource: "parties.Address",
      scope: "local",
      baseFilter: { party: { exact: "party_7" } },
      createDefaults: { party: "party_7" },
    });
    const form = pageChildren(props.children)
      .map((child) => pageElementProps<FormProps>(child, "form"))
      .find((candidate): candidate is FormProps => Boolean(candidate));
    expect(parsePageFields(form?.children)).toMatchObject([
      { name: "party", readOnly: true },
      { name: "label" },
      { name: "street" },
      { name: "extended" },
      { name: "po_box" },
      { name: "city" },
      { name: "region" },
      { name: "postal_code" },
      { name: "country" },
      { name: "is_primary", widget: "switch" },
    ]);
  });

  test("shows the empty copy its Party subtype supplies", () => {
    const view = PartyAddresses({ recordId: "party_7", emptyContent: "No addresses yet." });
    const list = pageChildren((view.props as { children?: ReactNode }).children)
      .map((child) => pageElementProps<ListProps>(child, "list"))
      .find((candidate): candidate is ListProps => Boolean(candidate));
    expect(list?.emptyContent).toBe("No addresses yet.");
  });
});
