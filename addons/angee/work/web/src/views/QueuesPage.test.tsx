// @vitest-environment happy-dom

import {
  AppRuntimeProvider,
  defaultWidgets,
  ResourceViewProvider,
  ToastProvider,
  type ListProps,
  type RecordPanelContext,
} from "@angee/ui";
import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...await importOriginal<typeof import("@tanstack/react-router")>(),
  useNavigate: () => vi.fn(),
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const ui = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...ui,
    useEnumOptions: () => [],
    useRouteHref: () => () => "/work/queues/queue_1",
    useRouteRecordId: () => "queue_1",
    ResourceList: ({ recordTabs }: {
      recordTabs: readonly { render: (context: Pick<RecordPanelContext, "recordId">) => React.ReactNode }[];
    }) => recordTabs[0]!.render({ recordId: "queue_1" }),
    DrawerResourceList: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    Form: () => null,
    List: (props: ListProps) => (
      <ui.List
        {...props}
        list={({ columns }) => (
          <ui.RowsListView
            rows={[
              { id: "triage", category: "TRIAGE" },
              { id: "backlog", category: "BACKLOG" },
            ]}
            columns={columns}
          />
        )}
      />
    ),
  };
});

import { QueuesPage } from "./QueuesPage";

afterEach(cleanup);

test("queue stage category cells use work's system and custom category tones", () => {
  render(
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ToastProvider>
        <ResourceViewProvider scope="local"><QueuesPage /></ResourceViewProvider>
      </ToastProvider>
    </AppRuntimeProvider>,
  );

  for (const cell of screen.getAllByText("TRIAGE")) {
    expect(cell.className).toContain("bg-warning-soft");
  }
  for (const cell of screen.getAllByText("BACKLOG")) {
    expect(cell.className).toContain("bg-inset");
  }
});
