// @vitest-environment happy-dom
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { fetchExchange } from "@urql/core";
import { describe, expect, test, vi } from "vitest";

import { GraphQLClientProvider } from "../graphql-provider";
import { TEST_SCHEMA_SDL } from "../test-schema";
import {
  extractResourceFacetResults,
  useResourceFacets,
  type ResourceFacetSpec,
} from "./facets";

describe("extractResourceFacetResults", () => {
  test("normalizes aliased grouped buckets into facet options", () => {
    const facets: readonly ResourceFacetSpec[] = [
      { id: "state", groups: [{ field: "STATE", key: "state" }] },
      {
        id: "provider",
        groups: [
          { field: "PROVIDER", key: "providerId" },
          { field: "PROVIDER__NAME", key: "provider_Name" },
        ],
        valueKey: "providerId",
        labelKey: "provider_Name",
      },
    ];

    expect(
      extractResourceFacetResults(
        {
          facet0: {
            totalCount: 1,
            results: [{
              count: 3,
              key: { state: "OPEN" },
              filter: { state: { exact: "OPEN" } },
            }],
          },
          facet1: {
            totalCount: 1,
            results: [{
              count: 2,
              key: {
                providerId: "provider-openai",
                provider_Name: "OpenAI",
              },
              filter: { provider: { sqid: "provider-openai" } },
            }],
          },
        },
        facets,
      ),
    ).toEqual({
      state: {
        count: 3,
        totalCount: 1,
        options: [{
          value: "OPEN",
          label: "OPEN",
          count: 3,
          key: { state: "OPEN" },
          filter: { state: { exact: "OPEN" } },
        }],
      },
      provider: {
        count: 2,
        totalCount: 1,
        options: [{
          value: "provider-openai",
          label: "OpenAI",
          count: 2,
          key: {
            providerId: "provider-openai",
            provider_Name: "OpenAI",
          },
          filter: { provider: { sqid: "provider-openai" } },
        }],
      },
    });
  });
});

describe("useResourceFacets", () => {
  test("fetches multiple facets in one grouped GraphQL operation", async () => {
    const { fetch, bodies } = mockTransport({
      facet0: {
        totalCount: 2,
        results: [
          {
            count: 3,
            key: { state: "OPEN" },
            filter: { state: { exact: "OPEN" } },
          },
          {
            count: 1,
            key: { state: "CLOSED" },
            filter: { state: { exact: "CLOSED" } },
          },
        ],
      },
      facet1: {
        totalCount: 1,
        results: [{
          count: 2,
          key: { createdAtMonth: "2026-06-01T00:00:00+00:00" },
          filter: { createdAt: { month: "2026-06-01" } },
        }],
      },
    });
    const filter = { title: { iContains: "launch" } };
    const { result } = renderHook(
      () =>
        useResourceFacets("Sale", {
          filter,
          facets: [
            { id: "state", groups: [{ field: "STATE", key: "state" }] },
            {
              id: "created",
              groups: [{
                field: "CREATED_AT",
                key: "createdAtMonth",
                granularity: "month",
              }],
              pageSize: 10,
            },
          ],
        }),
      { wrapper: wrapperWith(fetch) },
    );

    await waitFor(() => expect(result.current.fetching).toBe(false));

    expect(result.current.facets.state?.options.map((option) => option.value))
      .toEqual(["OPEN", "CLOSED"]);
    expect(result.current.facets.created?.options[0]).toMatchObject({
      value: "2026-06-01T00:00:00+00:00",
      count: 2,
      filter: { createdAt: { month: "2026-06-01" } },
    });
    expect(compactGraphQL(bodies[0]?.query)).toContain(
      "facet0: saleGroups( groupBy: $groupBy0 pagination: $pagination0 filter: $filter )",
    );
    expect(compactGraphQL(bodies[0]?.query)).toContain(
      "facet1: saleGroups( groupBy: $groupBy1 pagination: $pagination1 filter: $filter )",
    );
    expect(bodies[0]?.variables).toEqual({
      groupBy0: [{ field: "STATE" }],
      pagination0: null,
      groupBy1: [{ field: "CREATED_AT", granularity: "month" }],
      pagination1: { offset: 0, limit: 10 },
      filter,
    });
  });
});

function mockTransport(payload: unknown) {
  const bodies: Array<{ query: string; variables: Record<string, unknown> }> = [];
  const fetch = vi.fn(async (url: string, init: RequestInit) => {
    if (String(url).includes("/csrf/")) {
      return new Response(JSON.stringify({ token: "t" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    if (init?.body) bodies.push(JSON.parse(String(init.body)));
    return new Response(JSON.stringify({ data: payload }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  return { fetch: fetch as unknown as typeof globalThis.fetch, bodies };
}

function wrapperWith(fetch: typeof globalThis.fetch) {
  return ({ children }: { children: ReactNode }) =>
    createElement(GraphQLClientProvider, {
      config: {
        public: {
          url: "/graphql/",
          sdl: TEST_SCHEMA_SDL,
          fetch,
          exchanges: [fetchExchange],
        },
      },
      schema: "public",
      children,
    });
}

function compactGraphQL(query: string | undefined): string {
  return query?.replace(/\s+/g, " ").trim() ?? "";
}
