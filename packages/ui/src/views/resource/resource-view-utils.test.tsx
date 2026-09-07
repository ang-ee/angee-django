import { describe, expect, it } from "vitest";
import { schemaFieldMetadataFromDataResources, type QueryField } from "@angee/metadata";
import { testDataResource, testResourceQuery } from "@angee/metadata/testing";
import { resolveTextFilterField } from "./resource-view-utils";

function metadata(fields: Record<string, QueryField>, rowModel: "client" | "server" = "server") {
  const resource = testDataResource("notes.Note", { recordRepresentation: "display_name", rowModel, query: testResourceQuery({ fields }) });
  return schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;
}
const text: QueryField = { kind: "scalar", scalar: "String", nullable: true, values: [], filter: { field: "display_name", scalar: "String", values: [], operators: ["exact", "iContains"] } };
describe("resolveTextFilterField", () => {
  it("uses the representation when its query field supports text search", () => {
    expect(resolveTextFilterField(metadata({ display_name: text }))).toBe("display_name");
  });
  it("selects an available text comparison when the representation is not searchable", () => {
    expect(resolveTextFilterField(metadata({ title: { ...text, filter: { ...text.filter!, field: "title" } } }))).toBe("title");
  });
  it("does not advertise unsupported search for either row model", () => {
    expect(resolveTextFilterField(metadata({}))).toBeNull();
    expect(resolveTextFilterField(metadata({}, "client"))).toBeNull();
  });
  it("uses the rows surface text control without resource metadata", () => {
    expect(resolveTextFilterField(null)).toBe("title");
  });
});
