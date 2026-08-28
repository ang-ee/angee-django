import { describe, expect, it } from "vitest";
import type { ModelMetadata } from "@angee/metadata";

import { resolveTextFilterField } from "./resource-view-utils";

function meta(over: {
  recordRepresentation?: string;
  rowModel?: "client" | "server";
  filterFields?: string[];
  fields?: Record<string, { kind: string; scalar?: string }>;
}): ModelMetadata {
  return {
    recordRepresentation: over.recordRepresentation,
    fields: over.fields ?? {},
    resource: {
      rowModel: over.rowModel ?? "server",
      filterFields: over.filterFields ?? [],
    },
  } as unknown as ModelMetadata;
}

describe("resolveTextFilterField", () => {
  it("uses the title field when the server resource declares it filterable", () => {
    expect(
      resolveTextFilterField(
        meta({ recordRepresentation: "display_name", filterFields: ["display_name", "status"] }),
      ),
    ).toBe("display_name");
  });

  it("falls back to the first filterable text field when the title is not filterable", () => {
    // The integrations regression: title (display_name) was not in filterable, so
    // a free-text search built an invalid where. Degrade to a filterable text
    // field instead of erroring.
    expect(
      resolveTextFilterField(
        meta({
          recordRepresentation: "display_name",
          filterFields: ["vendor", "impl_class", "status"],
          fields: {
            vendor: { kind: "relation" },
            impl_class: { kind: "scalar", scalar: "String" },
            status: { kind: "scalar", scalar: "String" },
          },
        }),
      ),
    ).toBe("impl_class");
  });

  it("keeps the title field for a client row model (in-memory search over any field)", () => {
    expect(
      resolveTextFilterField(
        meta({ recordRepresentation: "display_name", rowModel: "client", filterFields: [] }),
      ),
    ).toBe("display_name");
  });

  it("defaults to 'title' when there is no metadata", () => {
    expect(resolveTextFilterField(null)).toBe("title");
  });
});
