// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import type {
  ResourceTypeName,
  UseResourceListOptions,
  UseResourceListResult,
} from "@angee/data";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { RelatedRowsList } from "./RelatedRowsList";
import { DataViewProvider } from "./data-view-context";
import type { ListColumn } from "./ListInternals";
import type { StringIdRow } from "./data-view-surface";

const sdkMocks = vi.hoisted(() => ({
  resourceLists: [] as Array<{
    model: string;
    options: UseResourceListOptions<ResourceTypeName>;
  }>,
}));

vi.mock("@angee/data", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/data")>();
  return {
    ...actual,
    useResourceList: (
      model: string,
      options: UseResourceListOptions<ResourceTypeName>,
    ): UseResourceListResult => {
      sdkMocks.resourceLists.push({ model, options });
      return {
        rows: [{
          id: "message-1",
          subject: "Hello",
          provider: { id: "provider-child", name: "Child" },
        }],
        total: 1,
        pageCount: 1,
        page: 1,
        pageSize: options.pageSize ?? 50,
        pageInfo: undefined,
        hasNext: false,
        hasPrev: false,
        setPage: () => undefined,
        firstPage: () => undefined,
        nextPage: () => undefined,
        prevPage: () => undefined,
        lastPage: () => undefined,
        fetching: false,
        error: null,
        refetch: () => undefined,
      };
    },
  };
});

afterEach(() => cleanup());

beforeEach(() => {
  sdkMocks.resourceLists = [];
});

interface RelatedRow extends StringIdRow {
  subject?: string;
}

const columns: readonly ListColumn<RelatedRow>[] = [{ field: "subject" }];

describe("RelatedRowsList", () => {
  test("queries a related collection and renders it with local data-view state", async () => {
    render(
      <RelatedRowsList<RelatedRow>
        model="messaging.Message"
        recordId="thread-1"
        fields={["subject"]}
        filterFor={(id) => ({ thread: { sqid: id } })}
        order={{ sentAt: "ASC" }}
        pageSize={25}
        columns={columns}
      />,
    );

    expect(await screen.findByText("Hello")).toBeTruthy();
    expect(sdkMocks.resourceLists).toEqual([
      {
        model: "messaging.Message",
        options: {
          fields: ["id", "subject"],
          filter: { thread: { sqid: "thread-1" } },
          order: { sentAt: "ASC" },
          pageSize: 25,
          enabled: true,
        },
      },
    ]);
  });

  test("ignores ambient data-view filters in record panels", async () => {
    render(
      <DataViewProvider
        scope="local"
        initialState={{ filter: { provider: { sqid: "provider-parent" } } }}
      >
        <RelatedRowsList<RelatedRow>
          model="messaging.Message"
          recordId="thread-1"
          fields={["id", "subject"]}
          filterFor={(id) => ({ thread: { sqid: id } })}
          columns={columns}
        />
      </DataViewProvider>,
    );

    expect(await screen.findByText("Hello")).toBeTruthy();
  });
});
