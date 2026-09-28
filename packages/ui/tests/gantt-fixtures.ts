import { testDataResource, testQueryAxis, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import type { DataResourceFieldMetadata } from "@angee/metadata";

function scalar(name: string, type = "String"): DataResourceFieldMetadata {
  return { name, kind: "scalar", scalar: type, readable: true, creatable: false, updatable: false,
    nullable: false, requiredOnCreate: false, aggregatable: false };
}

export const ganttLane = testDataResource("example.Lane", {
  capabilities: ["list"], recordRepresentation: "name",
  fields: [scalar("id", "ID"), scalar("name"), scalar("code")],
  query: testResourceQuery({ fields: {
    id: testQueryField("id", { scalar: "ID", sort: { field: "id" } }),
    name: testQueryField("name", { sort: { field: "name" } }), code: testQueryField("code"),
  } }),
});

export const ganttRecord = testDataResource("example.Schedule", {
  capabilities: ["list"], rowModel: "server", roots: { aggregate: "schedules_aggregate" },
  typeNames: { filter: "ScheduleBoolExp", order: "ScheduleOrderBy" },
  recordRepresentation: "name", recordSearchFields: ["name"],
  fields: [scalar("id", "ID"), scalar("name"), scalar("start", "Date"), scalar("end", "Date"), scalar("status"), {
    ...scalar("lane"), kind: "relation", relationModelLabel: ganttLane.modelLabel, relationObject: true,
  }],
  query: testResourceQuery({ fields: {
    id: testQueryField("id", { scalar: "ID", sort: { field: "id" } }),
    name: testQueryField("name", { sort: { field: "name" },
      filter: { field: "name", scalar: "String", values: [], operators: ["exact", "iContains"] } }),
    start: testQueryField("start", { scalar: "Date" }), end: testQueryField("end", { scalar: "Date" }),
    status: testQueryField("status"),
    lane: testQueryField("lane", {
      kind: "relation", scalar: "ID", row: { path: "lane.id", paths: ["lane.id", "lane.name"] },
      relation: { model: ganttLane.modelLabel, identityPath: "lane.id", labelPath: "lane.name" },
      filter: { field: "lane", scalar: "ID", values: [], operators: ["exact", "inList", "isNull"] },
    }),
  }, axes: {
    lane: testQueryAxis("lane", { kind: "relation", identityPath: "lane.id", labelPath: "lane.name", paths: ["lane.id", "lane.name"],
      drill: { kind: "identity", field: "lane", valueKey: "lane_id", nullMode: "isNull", valueMap: [] },
    }),
    status: testQueryAxis("status"),
  } }),
});

export const ganttResources = [ganttRecord, ganttLane];
export const ganttLanes = [{ id: "lane-a", name: "Alpha", code: "A" }, { id: "lane-b", name: "Beta", code: "B" }];
export const scheduledRecord = {
  id: "schedule-a", name: "First interval", start: "2026-09-01", end: "2026-09-03", status: "active",
  lane: { id: "lane-a", name: "Alpha" },
};
