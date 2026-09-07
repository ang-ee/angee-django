import { expect, test } from "vitest";
import { print } from "graphql";
import { createAngeeHasuraDataProvider } from "./provider";
import { listQueryMeta } from "./list-query";
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
