// @vitest-environment happy-dom

import * as React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { CalendarViewSpec } from "./resource-view-types";
import type { FormViewProps } from "./FormView";
import type { ListViewProps } from "./resource-view-types";
import type { ResourceListCalendarSpec } from "./ResourceList";

// ResourceList owns the routed-create seam; mock its heavy children so the
// calendar-spec → ListView wiring and the quick-create seed → FormView defaults
// are the only things exercised.
const captured = vi.hoisted(() => ({
  listCalendar: undefined as CalendarViewSpec | undefined,
  formDefaults: undefined as Record<string, unknown> | undefined,
}));

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => ({}),
  useNavigate: () => vi.fn(),
}));

vi.mock("./ListView", () => ({
  ListView: (props: ListViewProps & { calendar?: CalendarViewSpec }) => {
    captured.listCalendar = props.calendar;
    return null;
  },
}));

vi.mock("./FormView", () => ({
  FormView: (props: FormViewProps) => {
    captured.formDefaults = props.defaultValues as Record<string, unknown> | undefined;
    return null;
  },
}));

vi.mock("./useBulkDelete", () => ({
  useBulkDelete: () => ({
    canDelete: false,
    isPending: false,
    isPreviewOpen: false,
    previewState: null,
    previewRecordCount: 0,
    previewBlockedRecordCount: 0,
    previewOverflowCount: 0,
    deleteInitiate: vi.fn(),
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
  }),
}));

import { ResourceList } from "./ResourceList";

const SPEC: ResourceListCalendarSpec = {
  sources: [{ document: {}, variables: () => ({}), select: () => [] } as never],
  createDefaults: (start, end) => ({
    start: start.toISOString(),
    end: end.toISOString(),
  }),
};

beforeEach(() => {
  captured.listCalendar = undefined;
  captured.formDefaults = undefined;
});
afterEach(cleanup);

describe("ResourceList calendar quick-create", () => {
  test("range-select seeds the create form defaults through the routed-create seam", () => {
    const onSelect = vi.fn();
    const { rerender } = render(
      <ResourceList
        resource="calendar.Event"
        columns={[]}
        formFields={[{ name: "title" }]}
        calendar={SPEC}
        onSelect={onSelect}
      />,
    );

    // A range select seeds the create defaults and asks to open a create record.
    act(() => {
      captured.listCalendar?.onSelectRange?.(
        new Date("2026-06-17T14:00:00.000Z"),
        new Date("2026-06-17T15:00:00.000Z"),
      );
    });
    expect(onSelect).toHaveBeenCalledWith(null);

    // Opening the create record renders the form seeded with the mapped defaults.
    rerender(
      <ResourceList
        resource="calendar.Event"
        columns={[]}
        formFields={[{ name: "title" }]}
        calendar={SPEC}
        onSelect={onSelect}
        creating
      />,
    );
    expect(captured.formDefaults).toEqual({
      start: "2026-06-17T14:00:00.000Z",
      end: "2026-06-17T15:00:00.000Z",
    });
  });
});
