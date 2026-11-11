import { afterEach, expect, test, vi } from "vitest";

import { GraphQLError, graphqlWireFetch, type GraphQLWireRequest } from "./testing-wire";

afterEach(() => { vi.restoreAllMocks(); });

const post = (query: string, variables: Record<string, unknown> = {}, operationName?: string) =>
  ({ method: "POST", body: JSON.stringify({ query, variables, operationName }) });

test("each root field answers from its handler with arguments resolved against the variables, keyed by alias", async () => {
  const requests: GraphQLWireRequest[] = [];
  const fetch = graphqlWireFetch({
    notes: (args) => [{ id: "n1", where: args.where }],
    notes_aggregate: () => ({ aggregate: { count: 1 } }),
  }, (request) => requests.push(request));
  const response = await fetch("/graphql/", post(
    "query ResourceList($where: notes_bool_exp) { notes(where: $where) { id } total: notes_aggregate { aggregate { count } } }",
    { where: { id: { _eq: "n1" } } },
  ));
  expect(await response.json()).toEqual({ data: {
    notes: [{ id: "n1", where: { id: { _eq: "n1" } } }], total: { aggregate: { count: 1 } },
  } });
  expect(requests).toMatchObject([{ operationName: "ResourceList", fields: ["notes", "notes_aggregate"] }]);
});

test("a handler's GraphQLError answers the request with that error", async () => {
  const fetch = graphqlWireFetch({ decide: () => { throw new GraphQLError("Changed.", { extensions: { code: "STALE_REVISION" } }); } });
  const response = await fetch("/graphql/", post("mutation { decide { ok } }"));
  expect(await response.json()).toEqual({ data: null, errors: [{ message: "Changed.", extensions: { code: "STALE_REVISION" } }] });
});

test.each([
  ["an unhandled root field", "query Probe { other { id } }", {}, "no handler answers other (Probe)"],
  ["a missing required variable", "query Probe($id: ID!) { notes(id: $id) { id } }", {}, "Probe omits its required variable $id"],
  ["an unparseable document", "query {", {}, "is not a GraphQL document"],
])("%s fails loudly", async (_case, query, variables, message) => {
  const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const fetch = graphqlWireFetch({ notes: () => [] });
  await expect(fetch("/graphql/", post(query, variables))).rejects.toThrow(message);
  expect(error).toHaveBeenCalledWith(expect.stringContaining(message));
});

test("a defaulted variable may be omitted and the session's CSRF probe is answered", async () => {
  const fetch = graphqlWireFetch({ notes: (args) => ({ limit: args.limit }) });
  const response = await fetch("/graphql/", post("query Probe($limit: Int! = 5) { notes(limit: $limit) { id } }"));
  expect((await response.json()).data.notes).toEqual({ limit: undefined });
  expect(await (await fetch("/auth/csrf/")).json()).toEqual({ token: "test" });
});
