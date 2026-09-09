import { describe, expect, test } from "vitest";
import { Filter } from "./filter";
import { ResourceQuery, QueryParseError } from "./query";
import type { QueryAxis, QueryField, QueryDrill, FilterOperator } from "./query-schema";
import { testDataResource, testResourceQuery } from "./testing";

const channel: QueryAxis = {
  field: "channel", kind: "relation", identityPath: "channel.id", labelPath: "channel.display_name",
  paths: ["channel.id", "channel.display_name"],
  server: { input: "CHANNEL", key: "channel_id", labelInput: "CHANNEL__DISPLAY_NAME", labelKey: "channel__display_name" },
  drill: { kind: "identity", field: "channel", valueKey: "channel_id", nullMode: "isNull", valueMap: [] }, extractions: [],
};
const field = (name: string, scalar: string, operators: readonly FilterOperator[], extra: Partial<QueryField> = {}): QueryField => ({
  kind: "scalar", scalar, values: [], nullable: true, row: { path: name, paths: [name] }, filter: { field: name, scalar, values: [], operators }, ...extra,
});
const resource = () => testDataResource("messaging.Message", { query: testResourceQuery({
  fields: {
    channel: field("channel", "ID", ["exact", "inList", "isNull"], { kind: "relation", relation: { model: "messaging.Channel", identityPath: "channel.id", labelPath: "channel.display_name" } }),
    body: field("body", "String", ["exact", "ne", "inList", "notInList", "isNull", "contains", "iContains", "startsWith", "like", "iLike", "notLike", "notILike"]),
    count: field("message_count", "Int", ["exact", "gte", "lt"], { sort: { field: "message_count" } }),
    metadata: field("metadata", "JSON", ["jsonContains", "isNull"]),
    sent_at: field("sent_at", "DateTime", ["gte", "lt", "isNull"]),
  }, axes: { channel }, sort: { default: [{ field: "count", direction: "DESC" }] },
}) });

describe("ResourceQuery", () => {
  test("builds once per immutable resource", () => {
    const metadata = resource();
    expect(ResourceQuery.from(metadata)).toBe(ResourceQuery.from(metadata));
  });
  test("separates identity, label, selected row paths and server bucket names", () => {
    const query = ResourceQuery.from(resource());
    const axis = query.axis("channel");
    const rows = [{ channel: { id: "chn_a", display_name: "Team" } }, { channel: { id: "chn_b", display_name: "Team" } }, { channel: null }];
    expect(rows.map((row) => axis.identity(row))).toEqual(["chn_a", "chn_b", null]);
    expect(rows.map((row) => axis.label(row))).toEqual(["Team", "Team", null]);
    expect(query.selection([{ field: "channel" }])).toEqual(["channel.id", "channel.display_name"]);
    expect(axis.groupBy()).toEqual({
      dimensions: [{ input: "CHANNEL", key: "channel_id" }, { input: "CHANNEL__DISPLAY_NAME", key: "channel__display_name" }],
      valueKey: "channel_id", labelKey: "channel__display_name", orderBy: [
        { field: "channel__display_name", direction: "ASC", nulls: "LAST" },
        { field: "channel_id", direction: "ASC", nulls: "LAST" },
      ],
    });
    expect(query.toWhere(axis.drill({ key: { channel_id: "chn_a" } }))).toEqual({ channel: { _eq: "chn_a" } });
    expect(axis.drill({ key: { channel_id: null } })).toEqual({ channel: { isNull: true } });
    expect(() => axis.identity({})).toThrow(/missing selected identity path/);
    expect(() => axis.identity({ channel: { id: {} } })).toThrow(/must be a scalar/);
    expect(() => axis.label({ channel: { id: "chn_a" } })).toThrow(/missing selected label path/);
  });
  test("distinguishes scalar relations, client-only and summary-only axes", () => {
    const query = ResourceQuery.from(testDataResource("storage.File", { query: testResourceQuery({ axes: {
      drive: { ...channel, field: "drive", identityPath: "drive", labelPath: null, paths: ["drive"], server: null, drill: null },
      owner: { ...channel, field: "owner", identityPath: null, labelPath: null, paths: [], drill: null },
    } }) }));
    expect(query.axis("drive").identity({ drive: "drv_a" })).toBe("drv_a");
    expect(() => query.axis("drive").groupBy()).toThrow(/does not support server/);
    expect(query.axis("owner").drill({ key: { channel_id: "chn_a" } })).toBeUndefined();
    expect(() => query.axis("owner").selection).toThrow(/does not support client/);
  });
  test("rejects legacy aliases, unknown fields and unsupported operators", () => {
    const query = ResourceQuery.from(resource());
    for (const filter of [{ channel: { sqid: "a" } }, { channel: { id: "a" } }, { channel: { _eq: "a" } }, { missing: "x" }, { body: { regex: ".*" } }]) expect(() => query.filterFrom(filter)).toThrow(QueryParseError);
    expect(() => query.groupsFrom([{ field: "channel", aggregateKey: "channel_id" }])).toThrow(QueryParseError);
    expect(() => query.groupsFrom([{ field: "channel" }, { field: "channel" }])).toThrow(/duplicate/);
    expect(query.groupsFrom([{ field: "channel.display_name" }]).map((axis) => axis.id)).toEqual(["channel"]);
    expect(() => query.groupsFrom([{ field: "channel.nope" }])).toThrow(/unknown group/);
    expect(() => query.groupsFrom([{ field: "channel__display_name" }])).toThrow(/unknown group/);
  });
  test("preserves empty membership, null predicates and boolean branches", () => {
    const query = ResourceQuery.from(resource());
    expect(query.toWhere()).toEqual({});
    expect(query.toWhere({ body: { inList: [] } })).toEqual({ body: { _in: [] } });
    expect(query.toWhere({ OR: [] })).toEqual({ _or: [] });
    expect(query.toWhere({ AND: [] })).toEqual({ _and: [] });
    expect(query.toWhere({ NOT: { channel: null } })).toEqual({ _not: { channel: { _is_null: true } } });
    expect(() => query.toWhere({ body: { exact: null } })).toThrow(/use isNull/);
    expect(() => query.toWhere({ body: { inList: [null] } })).toThrow(/use isNull/);
    expect(() => query.toWhere({ body: { isNull: "true" } })).toThrow(/expected a boolean/);
    expect(() => query.toWhere({ count: { exact: 1.5 } })).toThrow(/integer/);
  });
  test("encodes text without overwriting comparisons sharing a wire operator", () => {
    const query = ResourceQuery.from(resource());
    expect(query.toWhere({ body: { contains: "50%_\\", startsWith: "Sale" } })).toEqual({ _and: [
      { body: { _like: "%50\\%\\_\\\\%" } }, { body: { _like: "Sale%" } },
    ] });
    expect(query.toWhere({ body: { iLike: "ab_%" } })).toEqual({ body: { _ilike: "ab_%" } });
  });
  test("uses filter input types and enum mappings independently of output symbols", () => {
    const query = ResourceQuery.from(testDataResource("notes.Note", { query: testResourceQuery({ fields: {
      state: field("state", "String", ["exact"], { kind: "enum", values: [{ value: "IN_REVIEW" }], filter: {
        field: "state", scalar: "String", values: [], operators: ["exact"], valueMap: [{ from: "IN_REVIEW", to: "in_review" }],
      } }),
    } }) }));
    expect(query.toWhere({ state: { exact: "in_review" } })).toEqual({ state: { _eq: "in_review" } });
    expect(query.matches({ state: "IN_REVIEW" }, { state: { exact: "in_review" } })).toBe(true);
  });
  test("requires selected values and distinguishes absent JSON keys from omitted selections", () => {
    const query = ResourceQuery.from(resource());
    expect(() => query.matches({}, { body: { isNull: true } })).toThrow(/missing selected value path/);
    expect(query.matches({ body: null }, { body: { isNull: true } })).toBe(true);
    const jsonQuery = ResourceQuery.from(testDataResource("notes.Note", { query: testResourceQuery({
      fields: { "metadata.mailbox": field("metadata.mailbox", "String", ["exact", "isNull"], { row: { path: "metadata.mailbox", paths: ["metadata"] } }) },
      axes: { "metadata.mailbox": { field: "metadata.mailbox", kind: "json", identityPath: "metadata.mailbox", paths: ["metadata"], extractions: [] } },
    }) }));
    expect(jsonQuery.matches({ metadata: {} }, { "metadata.mailbox": { isNull: true } })).toBe(true);
    expect(jsonQuery.axis("metadata.mailbox").identity({ metadata: {} })).toBeNull();
    expect(() => jsonQuery.axis("metadata.mailbox").identity({})).toThrow(/missing selected identity path/);
  });
  test("sorts only via declared canonical paths and exact wire keys", () => {
    const query = ResourceQuery.from(resource());
    expect(query.toOrderBy()).toEqual({ message_count: "desc" });
    expect(query.toOrderBy({ count: "ASC" })).toEqual({ message_count: "asc" });
    expect(query.toSorters([{ field: "count", direction: "DESC" }])).toEqual([{ field: "message_count", order: "desc" }]);
    expect(() => query.toOrderBy({ message_count: "asc" })).toThrow(/cannot be sorted/);
    expect(() => query.toOrderBy({ count: "sideways" })).toThrow(/ASC\/DESC/);
  });
  test("removes selected facet constraints while preserving other boolean branches", () => {
    const query = ResourceQuery.from(resource());
    expect(query.toFacet("channel", { channel: { exact: "a" }, body: { contains: "urgent" } }).where).toEqual({ body: { _like: "%urgent%" } });
    expect(query.withoutFields({ OR: [{ channel: "a" }, { body: "urgent" }] }, ["channel"])).toEqual({ OR: [{}, { body: { exact: "urgent" } }] });
    expect(query.withoutFields({ NOT: { channel: "a" } }, ["channel"])).toEqual({});
  });
});

describe("date and JSON axes", () => {
  test("shares UTC identity with half-open aggregate-owned range predicates", () => {
    const metadata = resource();
    const drill: QueryDrill = { kind: "range", field: "sent_at", valueKey: "sent_at_month", rangeKey: "sent_at_month_range", nullMode: "isNull", valueMap: [] };
    metadata.query.axes.sent_at = { field: "sent_at", kind: "date", identityPath: "sent_at", paths: ["sent_at"], server: { input: "SENT_AT", key: "sent_at" }, extractions: [{ name: "month", input: "MONTH", key: "sent_at_month", rangeKey: "sent_at_month_range", drill }] };
    const query = ResourceQuery.from(metadata);
    const axis = query.axis("sent_at", "month");
    const bucket = { key: { sent_at_month: "2026-02-01 00:00:00", sent_at_month_range: { from: "2026-02-01", to: "2026-03-01" } } };
    expect(axis.identity({ sent_at: "2026-03-01T00:30:00+02:00" })).toBe("2026-02");
    expect(axis.bucketIdentity(bucket)).toBe("2026-02");
    expect(query.toWhere(axis.drill(bucket))).toEqual({ sent_at: { _gte: "2026-02-01T00:00:00.000Z", _lt: "2026-03-01T00:00:00.000Z" } });
    expect(() => axis.drill({ key: { sent_at_month: "2026-02-01" } })).toThrow(/missing range/);
    expect(axis.drill({ key: { sent_at_month: null } })).toEqual({ sent_at: { isNull: true } });
  });
  test("separates JSON row paths from legal selections and SQL null from JSON null", () => {
    const metadata = resource();
    metadata.query.axes["metadata.mailbox"] = { field: "metadata.mailbox", kind: "json", identityPath: "metadata.mailbox", paths: ["metadata"], server: { input: "METADATA__MAILBOX", key: "metadata__mailbox" }, extractions: [], drill: { kind: "json", field: "metadata", valueKey: "metadata__mailbox", jsonPath: "mailbox", nullMode: "value", valueMap: [] } };
    const query = ResourceQuery.from(metadata);
    const axis = query.axis("metadata.mailbox");
    expect(axis.selection).toEqual(["metadata"]);
    expect(axis.identity({ metadata: { mailbox: "Team" } })).toBe("Team");
    expect(query.toWhere(axis.drill({ key: { metadata__mailbox: null } }))).toEqual({ metadata: { _contains: { mailbox: null } } });
  });
});

describe("local queries and filter values", () => {
  const query = ResourceQuery.forRows({ fields: {
    body: { scalar: "String" }, count: { scalar: "Int" }, metadata: { kind: "json" }, sent_at: { scalar: "DateTime" }, owner: { kind: "relation", identityPath: "owner.key", labelPath: "owner.name" },
  } });
  test("evaluates scalar, relation, wildcard and JSON filters at the same owner", () => {
    const row = { body: "Alpha%beta", count: 3, metadata: { mailbox: null, tags: ["urgent", "team"] }, owner: { key: "usr_a", name: "A" } };
    expect(query.matches(row, { body: { iContains: "ALPHA%" }, count: { gte: 3 } })).toBe(true);
    expect(query.matches(row, { owner: { exact: "usr_a" } })).toBe(true);
    expect(query.matches(row, { metadata: { jsonContains: { mailbox: null, tags: ["team"] } } })).toBe(true);
    expect(query.matches(row, { metadata: { jsonContains: { tags: ["missing"] } } })).toBe(false);
    expect(query.matches(row, { body: { like: "Alpha\\%b_ta" } })).toBe(true);
    expect(query.matches(row, { body: { like: "beta" } })).toBe(true);
  });
  test("matches native null, negation and empty collection semantics", () => {
    const row = { body: null };
    expect(query.matches(row, { body: { isNull: true } })).toBe(true);
    expect(query.matches(row, { body: { ne: "x" } })).toBe(true);
    expect(query.matches(row, { body: { notInList: [] } })).toBe(true);
    expect(query.matches(row, { NOT: { body: { exact: "x" } } })).toBe(true);
    expect(query.matches(row, { body: { contains: "" } })).toBe(false);
    expect(query.matches(row, { body: { inList: [] } })).toBe(false);
    expect(query.matches(row, { OR: [] })).toBe(false);
    expect(query.matches(row, { AND: [] })).toBe(true);
  });
  test("does not interpret free text as dates or guess relation identities", () => {
    expect(query.axis("body").identity({ body: "2026-09-07" })).toBe("2026-09-07");
    expect(query.axis("sent_at", "week").identity({ sent_at: "2026-09-06T20:00:00Z" })).toBe("2026-08-31");
    expect(() => query.axis("owner").identity({ owner: { id: "wrong-field" } })).toThrow(/owner.key/);
  });
  test("copies caller state and preserves conflicting constraints", () => {
    const original = { body: { exact: "urgent" } };
    const filter = Filter.from(original);
    original.body.exact = "changed";
    expect(filter.value).toEqual({ body: { exact: "urgent" } });
    const conflict = Filter.combine({ body: "a" }, { body: "b" });
    expect(query.matches({ body: "a" }, conflict)).toBe(false);
    expect(query.toWhere(conflict)).toEqual({ _and: [{ body: { _eq: "a" } }, { body: { _eq: "b" } }] });
  });
  test("toggles facets with one identity comparison shape", () => {
    const facet = { field: "channel", value: "chn_a" };
    const first = Filter.from().toggleFacet(facet);
    expect(first).toEqual({ channel: { exact: "chn_a" } });
    expect(Filter.from(first).toggleFacet({ ...facet, value: "chn_b" })).toEqual({ channel: { inList: ["chn_a", "chn_b"] } });
    expect(Filter.from(first).toggleFacet(facet)).toEqual({});
  });
});

test("editing or clearing text preserves sibling comparisons", () => {
  const original = { title: { startsWith: "A", iContains: "old", isNull: false }, active: true };
  const edited = Filter.from(original).withTextTerm("  new  ", "title");
  expect(edited).toEqual({ title: { startsWith: "A", iContains: "new", isNull: false }, active: { exact: true } });
  expect(Filter.from(edited).withTextTerm("  ", "title")).toEqual({ title: { startsWith: "A", isNull: false }, active: { exact: true } });
  expect(Filter.from({ name: { iContains: "text" } }).withTextTerm("", "name")).toEqual({});
  expect(original.title.iContains).toBe("old");
});

test("facet toggles preserve unrelated comparisons on the same field", () => {
  const facet = { field: "body", value: "alpha" };
  const initial = { body: { ne: "archived" } };
  const selected = Filter.from(initial).toggleFacet(facet);
  expect(selected).toEqual({ body: { exact: "alpha", ne: "archived" } });
  expect(Filter.from(selected).toggleFacet(facet)).toEqual(initial);
});

test("datetime equality and groups compare instants without losing microseconds", () => {
  const query = ResourceQuery.forRows({ fields: { time: { scalar: "DateTime" } } });
  const row = { time: "2026-09-07T10:00:00Z" };
  expect(query.matches(row, { time: { exact: "2026-09-07T12:00:00+02:00" } })).toBe(true);
  expect(query.matches(row, { time: { inList: ["2026-09-07T12:00:00+02:00"] } })).toBe(true);
  expect(query.matches(row, { time: { exact: "2026-09-07T10:00:00.000001Z" } })).toBe(false);
  expect(query.axis("time").identity(row)).toBe(query.axis("time").identity({ time: "2026-09-07T12:00:00+02:00" }));
});


test("date operands follow native calendar types without JavaScript rollover", () => {
  const query = ResourceQuery.forRows({ fields: { date: { scalar: "Date" }, time: { scalar: "DateTime" } } });
  for (const value of ["2026-02-30", "2026-02-29", "0000-01-01", "2026-13-01", "2026-09-07\n"]) {
    expect(() => query.filterFrom({ date: value })).toThrow(/ISO date/);
    expect(() => query.filterFrom({ time: `${value}T00:00:00Z` })).toThrow(/ISO date/);
  }
  expect(() => query.filterFrom({ date: "2026-09-07T12:00:00Z" })).toThrow(/ISO date/);
  expect(() => query.filterFrom({ time: "2026-09-07\n" })).toThrow(/ISO date/);
  expect(() => query.filterFrom({ time: { inList: ["2026-09-07T12:34:56Z\n"] } })).toThrow(/ISO date/);
  expect(query.filterFrom({ date: "2024-02-29" })).toEqual({ date: { exact: "2024-02-29" } });
  expect(query.matches({ time: "2026-09-07T00:00:00Z" }, { time: "2026-09-07" })).toBe(true);
  expect(query.matches({ time: "2026-09-08T00:00:00Z" }, { time: "2026-09-07T24:00:00Z" })).toBe(true);
  expect(query.matches({ time: "2026-09-07T00:00:00.123456Z" }, { time: "2026-09-07T00:00:00.123456789Z" })).toBe(true);
  expect(() => query.axis("time", "month").identity({ time: "2026-02-30" })).toThrow(/invalid date/);
});

test("numeric filters reject values outside native GraphQL input domains", () => {
  const query = ResourceQuery.forRows({ fields: { count: { scalar: "Int" }, ratio: { scalar: "Float" } } });
  for (const value of [-(2 ** 31), 0, 2 ** 31 - 1]) {
    expect(query.toWhere({ count: { exact: value, inList: [value] } }))
      .toEqual({ count: { _eq: value, _in: [value] } });
  }
  for (const value of [-(2 ** 31) - 1, 2 ** 31, 1.5, NaN, Infinity]) {
    for (const operand of [{ exact: value }, { inList: [value] }, { notInList: [value] }]) {
      expect(() => query.toWhere({ count: operand })).toThrow(QueryParseError);
    }
  }
  expect(query.toWhere({ ratio: { exact: Number.MAX_VALUE } })).toEqual({ ratio: { _eq: Number.MAX_VALUE } });
  for (const value of [NaN, Infinity, -Infinity]) {
    expect(() => query.toWhere({ ratio: { exact: value } })).toThrow(QueryParseError);
  }
});

test("time filters validate native ISO clocks without losing offsets or precision", () => {
  const query = ResourceQuery.forRows({ fields: { time: { scalar: "Time" } } });
  for (const value of ["00:00", "24:00", "23:59:59.123456789", "12:34:56Z", "12:34:56+05:30:15.123456"]) {
    expect(query.toWhere({ time: { exact: value, inList: [value] } }))
      .toEqual({ time: { _eq: value, _in: [value] } });
  }
  for (const value of ["not-a-time", "25:00", "24:01", "24:00:00.001", "12:60", "12:34:60", "12:34:56+24:00", "12:34\n"]) {
    expect(() => query.toWhere({ time: { exact: value } })).toThrow(/ISO time/);
    expect(() => query.toWhere({ time: { inList: [value] } })).toThrow(/ISO time/);
  }
});

test("decimal predicates and specialized sorting preserve arbitrary wire precision", () => {
  const query = ResourceQuery.forRows({ fields: { amount: { scalar: "Decimal" }, name: { scalar: "String" } } });
  const row = { amount: "9007199254740993.000000000000000001" };
  expect(query.matches(row, { amount: { exact: "9007199254740993.000000000000000002" } })).toBe(false);
  expect(query.matches(row, { amount: { lt: "9007199254740993.000000000000000002" } })).toBe(true);
  expect(query.matches(row, { amount: { inList: ["9007199254740993.000000000000000002"] } })).toBe(false);
  expect(query.matches({ amount: "0.00012" }, { amount: { exact: "+1.20e-4" } })).toBe(true);
  expect(query.matches({ amount: "-12" }, { amount: { lt: "-1.2" } })).toBe(true);
  expect(query.matches({ amount: "-0.00" }, { amount: { exact: "0" } })).toBe(true);
  expect(query.matches({ amount: "1e9999999" }, { amount: { gt: "1e9999998" } })).toBe(true);
  expect(query.toWhere({ amount: { exact: 1.25 } })).toEqual({ amount: { _eq: "1.25" } });
  const compare = query.comparator("amount")!;
  expect(compare(row, { amount: "9007199254740993.000000000000000002" })).toBeLessThan(0);
  expect(compare({ amount: null }, { amount: "1" })).toBeGreaterThan(0);
  expect(compare({ amount: null }, { amount: null })).toBe(0);
  expect(query.comparator("name")).toBeUndefined();
});
