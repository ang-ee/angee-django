import { describe, expect, test } from "vitest";
import { parsePageFields } from "@angee/ui";

import type { ReactNode } from "react";
import { AddressFields, PartyAddresses } from "./PartyAddresses";

describe("PartyAddresses", () => {
  test("owns a scoped create/edit form with the complete postal address", () => {
    const view = PartyAddresses({ recordId: "party_7" });
    const props = view.props as { children?: ReactNode; [key: string]: unknown };
    expect(props).toMatchObject({
      resource: "parties.Address",
      baseFilter: { party: { exact: "party_7" } },
      createDefaults: { party: "party_7" },
    });
    const addressFields = AddressFields({}).props as { children?: ReactNode };
    expect(parsePageFields(addressFields.children)).toMatchObject([
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
});
