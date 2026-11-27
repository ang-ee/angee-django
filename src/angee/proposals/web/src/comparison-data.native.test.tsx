// @vitest-environment happy-dom

import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { testDataResource } from "@angee/metadata/testing";
import { createAngeeHasuraDataProvider } from "@angee/refine";
import { createUiTestProviders } from "@angee/ui/testing";
import type { ReactNode } from "react";
import { afterEach, expect, test } from "vitest";

import { useRoundComparisonData } from "./comparison-data";
import { ANSWER_MODEL, PROPOSAL_MODEL, ROUND_MODEL, TOPIC_MODEL } from "./resources";

const { Provider, clearClients } = createUiTestProviders({
  resources: [ROUND_MODEL, TOPIC_MODEL, PROPOSAL_MODEL, ANSWER_MODEL].map((model) => testDataResource(model)),
  queryClientConfig: { defaultOptions: { queries: { retry: false } } },
});
afterEach(() => { cleanup(); clearClients(); });

test("the comparison's two-key orders reach the server as lists in their priority, through the patched Hasura provider", async () => {
  const requests: { query: string; variables: Record<string, unknown> }[] = [];
  const dataProvider = createAngeeHasuraDataProvider({
    url: "https://example.invalid/graphql",
    auth: (fetch) => fetch,
    fetch: async (_input, init) => {
      requests.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify({ data: {
        rounds_by_pk: { id: "rnd_1" },
        topics: [{ id: "top_1" }], topics_aggregate: { aggregate: { count: 1 } },
        proposals: [{ id: "prp_1" }], proposals_aggregate: { aggregate: { count: 1 } },
        answers: [], answers_aggregate: { aggregate: { count: 0 } },
      } }), { headers: { "Content-Type": "application/json" } });
    },
  });
  const wrapper = ({ children }: { children: ReactNode }) => <Provider dataProvider={dataProvider}>{children}</Provider>;
  const { result } = renderHook(() => useRoundComparisonData("rnd_1"), { wrapper });
  await waitFor(() => expect([result.current.topics, result.current.proposals]).toEqual([[{ id: "top_1" }], [{ id: "prp_1" }]]));
  /** The variables of the read of `root`'s rows. */
  const read = (root: string) => requests.find(({ query }) => new RegExp(`\\b${root}\\s*\\(`).test(query))?.variables;
  // The stock provider merges sorters into one object; the install policy patches it (`patches/refinedev-hasura-7.0.1.patch`).
  const unpatched = "@refinedev/hasura is not patched: install it through packages/scripts/pnpm-policy.cjs";
  // Topics by their position, then key; proposals by state, then age: each key its own object, in that order.
  expect(read("topics"), unpatched).toMatchObject({ where: { _and: [{ round: { _eq: "rnd_1" } }] }, order_by: [{ sort_order: "asc" }, { key: "asc" }] });
  expect(read("proposals"), unpatched).toMatchObject({ where: { _and: [{ round: { _eq: "rnd_1" } }] }, order_by: [{ state: "asc" }, { created_at: "asc" }] });
});
