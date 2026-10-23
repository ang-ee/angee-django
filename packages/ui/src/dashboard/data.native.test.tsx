// @vitest-environment happy-dom

import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../testing";

import { useDashboardWidgetData } from "./data";
import type { WidgetSpec } from "./headless";

const custom = vi.fn(async () => ({ data: {} }));
const { Provider, dataProvider, clearClients } = createUiTestProviders({ dataProvider: { custom } });
const embeddedData: WidgetSpec["data"][] = [
  { shape: "none", binding: { dashboardKey: "test.home", widgetId: "upload" } },
  { shape: "resourceView", preset: "test.inbox" },
];

afterEach(() => {
  cleanup();
  clearClients();
  vi.restoreAllMocks();
});

test.each(embeddedData)("$shape widgets do not resolve a model or fetch dashboard rows", (data) => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const spec: WidgetSpec = {
    schemaVersion: 1, id: "embedded", kind: "test", kindVersion: 1, title: "Embedded widget",
    data, options: {}, x: 0, y: 0, w: 6, h: 4, isArchived: false,
  };

  const { result } = renderHook(() => useDashboardWidgetData(spec), { wrapper: Provider });

  expect(warn).not.toHaveBeenCalled();
  expect(dataProvider.getList).not.toHaveBeenCalled();
  expect(custom).not.toHaveBeenCalled();
  expect(result.current).toMatchObject({ error: null, fetching: false, rows: [] });
});
