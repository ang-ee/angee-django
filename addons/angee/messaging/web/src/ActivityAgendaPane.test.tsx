// @vitest-environment happy-dom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { formatDate } from "@angee/ui";

const mocks = vi.hoisted(() => ({
  result: { data: undefined, error: null } as { data: unknown; error: Error | null },
  useAuthoredQuery: vi.fn(),
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useNamespaceT:
      (_namespace: string, messages: Record<string, string>) =>
      (key: string) =>
        messages[key] ?? key,
    // Only tasks have a routed record page in this fixture.
    useResourceRecordHrefLookup: () => (model: string, id: string) =>
      model === "projects.Task" ? `/projects/tasks/${id}` : undefined,
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: mocks.useAuthoredQuery,
}));

import { ActivityAgendaPane } from "./ActivityAgendaPane";

function agendaRow(overrides: Record<string, unknown> = {}) {
  return {
    id: "act_1",
    summary: "Call the vendor",
    due_date: "2026-10-01",
    state: "overdue",
    status: "TODO",
    attachment: { label: "Launch plan", model_label: "projects.Task", record_id: "tsk_1" },
    ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 9, 6, 9, 0));
  mocks.result = { data: undefined, error: null };
  mocks.useAuthoredQuery.mockImplementation(() => mocks.result);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  mocks.useAuthoredQuery.mockReset();
});

describe("ActivityAgendaPane", () => {
  test("reads every overdue activity plus the next 30 days, live on activity changes", () => {
    render(<ActivityAgendaPane />);

    expect(mocks.useAuthoredQuery).toHaveBeenCalledWith(
      expect.anything(),
      { windowStart: "1970-01-01", windowEnd: "2026-11-06" },
      { models: ["messaging.ThreadActivity"] },
    );
  });

  test("renders compact items with record links, due dates, a marked overdue state and a count", () => {
    mocks.result = {
      error: null,
      data: {
        activity_agenda: [
          agendaRow(),
          agendaRow({
            id: "act_2",
            summary: "Send the draft",
            due_date: "2026-10-20",
            state: "planned",
            attachment: { label: "Acme", model_label: "parties.Party", record_id: "pty_1" },
          }),
        ],
      },
    };
    render(<ActivityAgendaPane />);

    const pane = screen.getByRole("region", { name: "Activities due" });
    expect(within(pane).getByRole("heading", { name: "Activities due" })).toBeTruthy();
    expect(within(pane).getByText("· 2")).toBeTruthy();
    const items = within(within(pane).getByRole("list")).getAllByRole("listitem");
    expect(items).toHaveLength(2);

    const [overdue, planned] = items as [HTMLElement, HTMLElement];
    expect(within(overdue).getByText("Call the vendor")).toBeTruthy();
    expect(within(overdue).getByRole("link", { name: "Launch plan" }).getAttribute("href"))
      .toBe("/projects/tasks/tsk_1");
    expect(overdue.textContent).toContain(formatDate("2026-10-01"));
    expect(within(overdue).getByText("Overdue")).toBeTruthy();

    // A record without a routed page keeps its label; a planned activity is unmarked.
    expect(within(planned).getByText("Send the draft")).toBeTruthy();
    expect(within(planned).queryByRole("link")).toBeNull();
    expect(planned.textContent).toContain("Acme");
    expect(planned.textContent).toContain(formatDate("2026-10-20"));
    expect(within(planned).queryByText("Planned")).toBeNull();
  });

  test("marks an activity due today", () => {
    mocks.result = { error: null, data: { activity_agenda: [agendaRow({ due_date: "2026-10-06", state: "today" })] } };
    render(<ActivityAgendaPane />);

    expect(screen.getByText("Today")).toBeTruthy();
    expect(screen.queryByText("Overdue")).toBeNull();
  });

  test("renders an empty state with a zero count", () => {
    mocks.result = { error: null, data: { activity_agenda: [] } };
    render(<ActivityAgendaPane />);

    expect(screen.getByText("· 0")).toBeTruthy();
    expect(screen.getByText("No activities due")).toBeTruthy();
    expect(screen.getByText("Overdue activities and activities due in the next 30 days.")).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
  });

  test("holds the count and shows a loading status until the first read settles", () => {
    render(<ActivityAgendaPane />);

    expect(screen.getByRole("status").textContent).toContain("Loading activities");
    expect(screen.queryByText(/^· /)).toBeNull();
  });

  test("surfaces a failed read", () => {
    mocks.result = { data: undefined, error: new Error("Agenda unavailable") };
    render(<ActivityAgendaPane />);

    expect(screen.getByRole("alert").textContent).toContain("Agenda unavailable");
    expect(screen.queryByRole("status")).toBeNull();
  });
});
