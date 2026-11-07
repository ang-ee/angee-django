import { expect, test } from "vitest";
import { print } from "graphql";
import { createAngeeHasuraDataProvider } from "./provider";
import { groupRowsDocument, listQueryMeta } from "./list-query";
import { extractGroupBy, groupByVariables } from "./operations";
import { refineFieldsFromPaths } from "./selections";

test("selects nested paths without losing relation identity or display leaves", () => {
  expect(refineFieldsFromPaths(["id", "channel.id", "channel.display_name", "channel.id"]))
    .toEqual(["id", { channel: ["id", "display_name"] }]);
  expect(() => refineFieldsFromPaths(["id) { secret }"])).toThrow();
});

test("native provider preserves compiled predicates, order, and pagination", async () => {
  let request: { query: string; variables: Record<string, unknown> } | undefined;
  const provider = createAngeeHasuraDataProvider({
    url: "https://example.invalid/graphql",
    auth: (fetch) => fetch,
    fetch: async (_url, options) => {
      request = JSON.parse(String(options?.body));
      return new Response(JSON.stringify({ data: { messages: [{ id: "m1" }], messages_aggregate: { aggregate: { count: 3 } } } }), { headers: { "Content-Type": "application/json" } });
    },
  });
  const where = { _and: [{ _not: { channel: { _is_null: true } } }, { id: { _in: [] } }] };
  const orderBy = [{ id: "desc" }];
  const target = { root: "messages", aggregateRoot: "message_stats", filterType: "MessageBoolExp", orderType: "MessageOrderBy", resourceIdentifier: "console.messaging.Message" };
  const meta = listQueryMeta(target, ["id", "channel.id", "channel.display_name"], where, orderBy);
  const result = await provider.getList({ resource: "messages", filters: [], sorters: [], pagination: { currentPage: 2, pageSize: 2, mode: "server" }, meta });
  expect(result).toEqual({ data: [{ id: "m1" }], total: 3 });
  expect(request?.variables).toEqual({ where, order_by: orderBy, limit: 2, offset: 2 });
  expect(request?.query).toContain("messages_aggregate: message_stats");
  expect(print(meta.gqlQuery)).toContain("display_name");
});

test("native list selections bind context arguments without changing response fields", async () => {
  let request: { query: string; variables: Record<string, unknown> } | undefined;
  const provider = createAngeeHasuraDataProvider({
    url: "https://example.invalid/graphql",
    auth: (fetch) => fetch,
    fetch: async (_url, options) => {
      request = JSON.parse(String(options?.body));
      return new Response(JSON.stringify({ data: {
        bank_accounts: [{ id: "bank_1", verification_state: "VERIFIED" }],
        bank_accounts_aggregate: { aggregate: { count: 1 } },
      } }), { headers: { "Content-Type": "application/json" } });
    },
  });
  const target = {
    root: "bank_accounts", aggregateRoot: "bank_accounts_aggregate",
    filterType: "BankAccountBoolExp", orderType: "BankAccountOrderBy",
  };
  const meta = listQueryMeta(target, ["id", "verification_state"], undefined, undefined, [{
    path: "verification_state",
    arguments: [{ name: "company_id", type: "ID!", value: "company_1" }],
  }]);

  await provider.getList({
    resource: "bank_accounts", filters: [], sorters: [],
    pagination: { currentPage: 1, pageSize: 20, mode: "server" }, meta,
  });

  expect(request?.variables).toMatchObject({ selection_0_0: "company_1", limit: 20, offset: 0 });
  expect(request?.query).toContain("verification_state(company_id: $selection_0_0)");
  expect(request?.query).toContain("$selection_0_0: ID!");
});

test("a groups document carries each group's first page of the list's fields", () => {
  const document = groupRowsDocument(
    "query NoteGroups($group_by: [NoteGroupBySpec!]!) { notes_groups(group_by: $group_by) { key { status } aggregate { count } } totalCount: notes_groups_count(group_by: $group_by) }",
    { root: "notes_groups", orderType: "NoteOrderBy" },
    ["id", "owner.id", "owner.display_name"],
  );
  const text = print(document);
  expect(text).toContain("$with_rows: Boolean = false, $rows_limit: Int = 1, $rows_order_by: [NoteOrderBy!]");
  expect(text).toContain("rows(limit: $rows_limit, order_by: $rows_order_by) @include(if: $with_rows) {");
  expect(text).toContain("owner {\n        id\n        display_name\n      }");
  // The count companion selects no rows.
  expect(text.match(/rows\(/g)).toHaveLength(1);
  expect(() => groupRowsDocument("query X { other { id } }", { root: "notes_groups", orderType: "NoteOrderBy" }, ["id"]))
    .toThrow("does not select notes_groups");

  expect(groupByVariables({ dimensions: [{ input: "STATUS" }], rows: { limit: 3, orderBy: { title: "asc" } } }))
    .toMatchObject({ with_rows: true, rows_limit: 3, rows_order_by: { title: "asc" } });
  expect(groupByVariables({ dimensions: [{ input: "STATUS" }] })).not.toHaveProperty("with_rows");
  const result = extractGroupBy({
    notes_groups: [{ key: { status: "open" }, aggregate: { count: 2 }, rows: [{ id: "n1" }, { id: "n2" }] }],
    totalCount: 1,
  }, "notes_groups");
  expect(result.buckets[0]?.rows).toEqual([{ id: "n1" }, { id: "n2" }]);
});

