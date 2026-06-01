// @vitest-environment happy-dom

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useState, type ReactElement } from "react";
import { NuqsTestingAdapter } from "nuqs/adapters/testing";
import { beforeAll, describe, expect, test, vi } from "vitest";

import {
  Breadcrumb,
  BreadcrumbProvider,
} from "../chrome/Breadcrumb";
import { DataPage } from "./DataPage";
import type { FormField } from "./FormView";
import type { ListColumn } from "./ListView";
import type {
  Row,
  ResourceTypeName,
  UseResourceListOptions,
  UseResourceListResult,
} from "@angee/sdk";

const sdkMocks = vi.hoisted(() => ({
  rows: [
    { id: "note-1", title: "First" },
    { id: "note-2", title: "Second" },
    { id: "note-3", title: "Third" },
    { id: "note-4", title: "Fourth" },
  ] satisfies Row[],
  mutate: vi.fn(async ({ data }: { data: Row }) => data),
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  const ReactRuntime = await import("react");
  return {
    ...actual,
    useResourceList: (
      _model: string,
      options: UseResourceListOptions<ResourceTypeName>,
    ): UseResourceListResult => {
      const pageSize = options.pageSize ?? 50;
      const pageCount = Math.max(
        1,
        Math.ceil(sdkMocks.rows.length / pageSize),
      );
      const requestedPage = Math.min(
        pageCount,
        Math.max(1, options.initialPage ?? 1),
      );
      const [page, setPageState] = ReactRuntime.useState(requestedPage);
      ReactRuntime.useEffect(() => {
        setPageState(requestedPage);
      }, [requestedPage]);
      const visiblePage = Math.min(page, pageCount);
      const rows = sdkMocks.rows.slice(
        (visiblePage - 1) * pageSize,
        visiblePage * pageSize,
      );
      const setPage = (next: number) => {
        setPageState(Math.min(pageCount, Math.max(1, Math.floor(next))));
      };
      return {
        rows,
        total: sdkMocks.rows.length,
        pageCount,
        page: visiblePage,
        pageSize,
        pageInfo: undefined,
        hasNext: visiblePage < pageCount,
        hasPrev: visiblePage > 1,
        setPage,
        firstPage: () => setPage(1),
        nextPage: () => setPage(visiblePage + 1),
        prevPage: () => setPage(visiblePage - 1),
        lastPage: () => setPage(pageCount),
        fetching: false,
        error: null,
        refetch: vi.fn(),
      };
    },
    useResourceRecord: (_model: string, id: string | null) => ({
      record: sdkMocks.rows.find((row) => row.id === id) ?? null,
      fetching: false,
      error: null,
      refetch: vi.fn(),
    }),
    useResourceMutation: () => [
      sdkMocks.mutate,
      { fetching: false, error: null },
    ],
    useWidget: () => undefined,
  };
});

const columns = [
  { field: "title", header: "Title" },
] satisfies readonly ListColumn[];

const formFields = [
  { name: "title", label: "Title", title: true },
] satisfies readonly FormField[];

describe("DataPage", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
  });

  test("renders record navigation and reuses the view switcher in record chrome", async () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();

    const view = render(
      <NuqsTestingAdapter>
        <DataPage
          model="notes.Note"
          columns={columns}
          formFields={formFields}
          recordId="note-2"
          placement="inline"
          pageSize={2}
          onSelect={onSelect}
          onClose={onClose}
        />
      </NuqsTestingAdapter>,
    );

    const pager = await screen.findByRole("navigation", {
      name: "Record navigation",
    });
    expect(pager.textContent?.replace(/\s+/g, " ").trim()).toContain(
      "2 of 4",
    );

    fireEvent.click(
      within(pager).getByRole("button", { name: "Next record" }),
    );
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("note-3"));
    await waitFor(() =>
      expect(
        screen.queryByRole("navigation", { name: "Record navigation" }),
      ).toBeNull(),
    );

    const switcher = screen.getByRole("group", {
      name: "Record view switcher",
    });
    const boardButton = within(switcher).getByRole("button", {
      name: "Board view",
    });
    fireEvent.click(boardButton);
    expect(onClose).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(boardButton.getAttribute("aria-pressed")).toBe("true"),
    );

    await act(async () => {
      view.unmount();
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 0);
      });
    });
  });

  test("publishes persistent breadcrumbs for the selected record", async () => {
    const view = render(
      <NuqsTestingAdapter>
        <BreadcrumbProvider initialTrail={[{ label: "Notes" }]}>
          <Breadcrumb />
          <DataPage
            model="notes.Note"
            columns={columns}
            formFields={formFields}
            recordId="note-2"
            placement="inline"
            pageSize={2}
          />
        </BreadcrumbProvider>
      </NuqsTestingAdapter>,
    );

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    await waitFor(() =>
      expect(within(breadcrumb).getByText("Second")).toBeTruthy(),
    );
    expect(within(breadcrumb).getByText("Second").getAttribute("aria-current"))
      .toBe("page");

    await act(async () => {
      view.unmount();
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 0);
      });
    });
  });

  test("opens a selected row and keeps the record breadcrumb published", async () => {
    function Harness(): ReactElement {
      const [recordId, setRecordId] = useState<string | null | undefined>(
        undefined,
      );
      return (
        <NuqsTestingAdapter>
          <BreadcrumbProvider initialTrail={[{ label: "Notes" }]}>
            <Breadcrumb />
            <DataPage
              model="notes.Note"
              columns={columns}
              formFields={formFields}
              recordId={recordId}
              placement="inline"
              pageSize={2}
              onSelect={setRecordId}
            />
          </BreadcrumbProvider>
        </NuqsTestingAdapter>
      );
    }

    const view = render(<Harness />);

    fireEvent.click(await screen.findByRole("button", { name: "Open First" }));
    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    await waitFor(() =>
      expect(within(breadcrumb).getByText("First")).toBeTruthy(),
    );
    expect(within(breadcrumb).getByText("First").getAttribute("aria-current"))
      .toBe("page");

    await act(async () => {
      view.unmount();
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 0);
      });
    });
  });

  test("lets the seeded default group be cleared", async () => {
    const view = render(
      <NuqsTestingAdapter>
        <DataPage
          model="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </NuqsTestingAdapter>,
    );

    const removeGroup = await screen.findByRole("button", {
      name: "Remove group",
    });
    fireEvent.click(removeGroup);

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Remove group" }),
      ).toBeNull(),
    );

    await act(async () => {
      view.unmount();
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 0);
      });
    });
  });

  test("lets the seeded default group granularity be changed", async () => {
    const view = render(
      <NuqsTestingAdapter>
        <DataPage
          model="notes.Note"
          columns={[...columns, { field: "updatedAt", header: "Updated At" }]}
          formFields={formFields}
          defaultGroup={{ field: "updatedAt", granularity: "day" }}
        />
      </NuqsTestingAdapter>,
    );

    fireEvent.click(
      await screen.findByRole("button", {
        name: "Filter, group, favorites",
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: "Month" }));

    await waitFor(() =>
      expect(screen.getByText("Updated · Month")).toBeTruthy(),
    );

    await act(async () => {
      view.unmount();
      await new Promise<void>((resolve) => {
        setTimeout(resolve, 0);
      });
    });
  });
});
