// @vitest-environment happy-dom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import { ModalsHost, ToastProvider } from "../../feedback";
import { AppRuntimeProvider } from "../../runtime";
import { defaultWidgets } from "../../widgets";
import { ganttResources, ganttLanes, ganttRecord, scheduledRecord } from "../../../tests/gantt-fixtures";
import { Column } from "../page";
import { ResourceList } from "./ResourceList";
import { List } from "./List";
import { ResourceViewProvider, useResourceView, type ResourceViewContextValue } from "./resource-view-context";
import { availableResourceViewKinds, resourceViewKindCapabilities } from "./resource-view-model";
import type { GanttViewProps } from "../gantt/GanttView";

const drawing = vi.hoisted(() => ({ props: null as GanttViewProps | null }));
vi.mock("../gantt/GanttView", () => ({ GanttView: (props: GanttViewProps) => {
  drawing.props = props;
  return <div aria-label="Schedule chart" />;
} }));
const { Provider, dataProvider, clearClients } = createUiTestProviders({
  resources: ganttResources,
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); drawing.props = null; });

describe("ResourceList Gantt declaration", () => {
  test("offers Gantt only with its data source and uses list filter/pager capabilities", () => {
    expect(availableResourceViewKinds()).not.toContain("gantt");
    expect(availableResourceViewKinds({ gantt: true })).toContain("gantt");
    expect(resourceViewKindCapabilities("gantt")).toEqual({
      grouping: true, pagination: true, columns: false, filter: true, requiresSources: true,
    });
  });

  test("composes the nested List declaration, opens bars by record id and keeps saved filter intent across kinds", async () => {
    dataProvider.getList.mockImplementation(async ({ resource }) => ({
      data: resource === "lanes" ? ganttLanes : [scheduledRecord], total: resource === "lanes" ? 2 : 1,
    }));
    const onSelect = vi.fn();
    const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
    let view: ResourceViewContextValue;
    function Collection() {
      view = useResourceView();
      return <ResourceList resource={ganttRecord.modelLabel} scope="inherit" onSelect={onSelect} hideCreate renderRecord={() => <div>Record details</div>}>
        <List gantt={{ start: "start", end: "end" }} laneSource={{ field: "lane" }}>
          <Column field="name" header="Name" />
        </List>
      </ResourceList>;
    }
    render(<RouterContextProvider router={router}><Provider><AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><ModalsHost><ToastProvider>
      <ResourceViewProvider resource={ganttRecord.modelLabel} scope="local" initialState={{ view: "gantt", anchor: "2026-09-01" }}>
        <Collection />
      </ResourceViewProvider>
    </ToastProvider></ModalsHost></AppRuntimeProvider></Provider></RouterContextProvider>);
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    const event = drawing.props?.events[0];
    if (!event) throw new Error("Missing fixture event");
    act(() => drawing.props?.onEventClick?.(event));
    expect(onSelect).toHaveBeenCalledWith("schedule-a", undefined);
    act(() => view.applyFavorite({
      id: "favorite:list", label: "List query", view: "list", groupStack: [], filter: { name: { iContains: "First" } },
    }));
    await waitFor(() => expect(screen.queryByLabelText("Schedule chart")).toBeNull());
    expect(view!.state.filter).toEqual({ name: { iContains: "First" } });
    act(() => view.setView("gantt"));
    await screen.findByLabelText("Schedule chart");
    expect(view!.state.filter).toEqual({ name: { iContains: "First" } });
    expect(view!.state.groupStack).toEqual([{ field: "lane" }]);
    await waitFor(() => expect(drawing.props?.events).toHaveLength(1));
    expect(dataProvider.update).not.toHaveBeenCalled();
  });
});
