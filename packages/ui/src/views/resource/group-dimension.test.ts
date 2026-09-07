// @vitest-environment happy-dom
import { describe, expect, test } from "vitest";
import { ResourceQuery, schemaFieldMetadataFromDataResources, type DataResourceFieldMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { bucketValueLabels, groupLabel, tableGroupAxes } from "./resource-view-list-body";

const TEST_T = (key: string, vars?: Record<string, unknown>): string => {
  if (key === "list.quarter") return `Q${vars?.quarter} ${vars?.year}`;
  if (key === "list.weekOf") return `Week of ${vars?.date}`;
  return key;
};
const contract = ResourceQuery.forRows({ fields: {
  status: { scalar: "String" }, party: { kind: "relation", identityPath: "party.id", labelPath: "party.display_name" },
  createdAt: { scalar: "DateTime" }, metadata: { kind: "json", scalar: "JSON" },
  "metadata.mailbox": { scalar: "String" }, nestedOwner: { scalar: "String" },
} }).contract;
contract.axes.status!.server = { input: "SERVER_STATUS", key: "serverStatus" };
contract.axes.status!.drill = { kind: "value", field: "status", valueKey: "serverStatus", nullMode: "isNull",
  valueMap: [{ from: "IN_REVIEW", to: "in_review" }] };
contract.axes.party!.server = { input: "PARTY", key: "partyId", labelInput: "PARTY__DISPLAY_NAME", labelKey: "party_DisplayName" };
contract.axes.party!.drill = { kind: "identity", field: "party", valueKey: "partyId", nullMode: "isNull", valueMap: [] };
contract.axes.createdAt!.server = { input: "CREATED_AT", key: "createdAt" };
contract.axes.createdAt!.extractions = contract.axes.createdAt!.extractions.map((extraction) => ({ ...extraction,
  key: `createdAt${extraction.name}`, rangeKey: `createdAt${extraction.name}Range`,
  drill: { kind: "range", field: "createdAt", valueKey: `createdAt${extraction.name}`, rangeKey: `createdAt${extraction.name}Range`, nullMode: "isNull", valueMap: [] },
}));
contract.axes.metadata!.server = { input: "METADATA", key: "metadata" };
contract.axes.metadata!.drill = { kind: "value", field: "metadata", valueKey: "metadata", valueTransform: "json", nullMode: "value", valueMap: [] };
contract.fields.metadata!.filter = { ...contract.fields.metadata!.filter!, operators: ["exact", "jsonContains", "isNull"] };
contract.axes["metadata.mailbox"]!.server = { input: "METADATA__MAILBOX", key: "metadata__mailbox" };
contract.axes["metadata.mailbox"] = { ...contract.axes["metadata.mailbox"]!, paths: ["metadata"] };
contract.axes["metadata.mailbox"]!.drill = { kind: "json", field: "metadata", valueKey: "metadata__mailbox", jsonPath: "mailbox", nullMode: "value", valueMap: [] };
contract.axes.nestedOwner!.server = { input: "NESTED_OWNER", key: "nestedOwner" };
const fields: DataResourceFieldMetadata[] = Object.entries(contract.fields).map(([name, field]) => ({
  name, kind: field.kind === "json" || field.kind === "object" ? "scalar" : field.kind, scalar: field.scalar, readable: true, aggregatable: false,
  creatable: false, updatable: false, requiredOnCreate: false,
}));
const metadata = schemaFieldMetadataFromDataResources([testDataResource("test.Row", { fields, query: contract })]).labels["test.Row"]!;
const query = ResourceQuery.from(metadata);

describe("resource query grouping projections", () => {
  test("uses declared wire inputs and keys without spelling inference", () => {
    expect(query.axis("status").groupBy()).toEqual({
      dimensions: [{ input: "SERVER_STATUS", key: "serverStatus" }], valueKey: "serverStatus",
      orderBy: [{ field: "serverStatus", direction: "ASC", nulls: "LAST" }],
    });
    expect(query.axis("party").groupBy()).toEqual({
      dimensions: [{ input: "PARTY", key: "partyId" }, { input: "PARTY__DISPLAY_NAME", key: "party_DisplayName" }],
      valueKey: "partyId", labelKey: "party_DisplayName",
      orderBy: [{ field: "party_DisplayName", direction: "ASC", nulls: "LAST" }],
    });
  });
  test("rejects stale aliases and unknown axes at the boundary", () => {
    expect(() => query.groupsFrom([{ field: "party.display_name" }])).toThrow("unknown group axis");
    expect(() => query.groupsFrom([{ field: "party", aggregateKey: "partyId" }])).toThrow();
    expect(() => query.group({ field: "missing" })).toThrow("unknown group axis");
  });
  test("date extraction carries the native key and range", () => {
    expect(query.axis("createdAt", "month").groupBy()).toMatchObject({
      dimensions: [{ input: "CREATED_AT", key: "createdAtmonth", granularity: "MONTH", rangeKey: "createdAtmonthRange" }],
      valueKey: "createdAtmonth", orderBy: [{ field: "createdAtmonth", direction: "ASC", nulls: "LAST" }],
    });
  });
  test("duplicate relation names preserve identities and independent predicates", () => {
    const first = { key: { partyId: "1", party_DisplayName: "Same" }, count: 1 };
    const second = { key: { partyId: "2", party_DisplayName: "Same" }, count: 1 };
    expect(bucketValueLabels(first, [{ field: "party" }], metadata, "No value", TEST_T)).toEqual(["Same"]);
    expect(bucketValueLabels(second, [{ field: "party" }], metadata, "No value", TEST_T)).toEqual(["Same"]);
    expect(query.toWhere(query.axis("party").drill(first))).toEqual({ party: { _eq: "1" } });
    expect(query.toWhere(query.axis("party").drill(second))).toEqual({ party: { _eq: "2" } });
  });
  test("null relation labels use the bounded empty relation copy", () => {
    expect(bucketValueLabels({ key: { partyId: null, party_DisplayName: null }, count: 1 }, [{ field: "party" }], metadata,
      "No value", TEST_T, (field) => `No ${field}`)).toEqual(["No party"]);
    expect(() => bucketValueLabels({ key: {}, count: 1 }, [{ field: "party" }], null, "No value", TEST_T)).toThrow("Resource metadata");
  });
});

describe("localized labels over stable identities", () => {
  test.each([["quarter", "2026-Q3"], ["month", "2026-08"], ["week", "2026-08-17"]])("%s is computed once by the axis", (granularity, identity) => {
    expect(query.axis("createdAt", granularity).identity({ createdAt: "2026-08-22T12:00:00Z" })).toBe(identity);
  });
  test("client and server date values use the same localized renderer", () => {
    const group = { field: "createdAt", granularity: "quarter" };
    const alternateT = (key: string, vars?: Record<string, unknown>) => key === "list.quarter" ? `${vars?.year} trimestre ${vars?.quarter}` : key;
    const key = query.group(group).identity({ createdAt: "2026-08-22T12:00:00Z" });
    expect(groupLabel(key, group, metadata, "None", TEST_T)).toBe("Q3 2026");
    expect(groupLabel(key, group, metadata, "None", alternateT)).toBe("2026 trimestre 3");
    expect(bucketValueLabels({ key: { createdAtmonth: "2026-02-01 00:00:00+00:00" }, count: 1 },
      [{ field: "createdAt", granularity: "month" }], metadata, "None", TEST_T)).toEqual(["February 2026"]);
  });
  test("date-like relation labels and scalar text stay verbatim", () => {
    expect(groupLabel("2026-09", { field: "party" }, metadata, "None", TEST_T)).toBe("2026-09");
    expect(groupLabel("CATC", { field: "party" }, metadata, "None", TEST_T)).toBe("CATC");
  });
  test("declared local rows use the same semantic axis without server dimensions", () => {
    const [axis] = tableGroupAxes([{ field: "createdAt", granularity: "month" }], null, [{ field: "createdAt" }]);
    expect(axis!.identity({ createdAt: "2026-09-01T00:00:00Z" })).toBe("2026-09");
    expect(() => axis!.groupBy()).toThrow("does not support server grouping");
  });
});

describe("bucket predicates", () => {
  test("date ranges remain half-open and normalize upstream date spelling", () => {
    expect(query.axis("createdAt", "month").drill({ key: { createdAtmonth: "2026-02-01 00:00:00+00:00", createdAtmonthRange: {
      from: "2026-02-01 00:00:00+00:00", to: "2026-03-01 00:00:00+00:00",
    } } })).toEqual({ createdAt: { gte: "2026-02-01T00:00:00.000Z", lt: "2026-03-01T00:00:00.000Z" } });
  });
  test("empty dates drill to isNull", () => {
    expect(query.axis("createdAt", "month").drill({ key: { createdAtmonth: "" } })).toEqual({ createdAt: { isNull: true } });
  });
  test("structured JSON and declared JSON paths retain their exact predicates", () => {
    expect(query.axis("metadata").drill({ key: { metadata: '{"kind":"note","flags":["pinned"]}' } }))
      .toEqual({ metadata: { exact: { kind: "note", flags: ["pinned"] } } });
    expect(query.axis("metadata.mailbox").drill({ key: { metadata__mailbox: "Sent Messages" } }))
      .toEqual({ metadata: { jsonContains: { mailbox: "Sent Messages" } } });
    expect(query.axis("metadata.mailbox").selection).toEqual(["metadata"]);
  });
  test("the contract maps bucket values to filter values", () => {
    expect(query.axis("status").drill({ key: { serverStatus: "IN_REVIEW" } })).toEqual({ status: { exact: "in_review" } });
  });
  test("server-only summary groups can have no drilldown", () => {
    expect(query.axis("nestedOwner").drill({ key: { nestedOwner: "Summary" } })).toBeUndefined();
  });
});
