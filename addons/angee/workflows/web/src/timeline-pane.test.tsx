// @vitest-environment happy-dom
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useRecordTimelineAttention } from "./timeline-pane";

const owner = vi.hoisted(() => ({
  query: vi.fn(), initial: vi.fn(), collapse: vi.fn(), active: vi.fn(),
  ids: ["first"], count: 1,
}));
vi.mock("@angee/refine", async (original) => ({ ...await original<typeof import("@angee/refine")>(), useAuthoredQuery: owner.query }));
vi.mock("@angee/decisions", async (original) => ({ ...await original<typeof import("@angee/decisions")>(), useDecisionFieldMarks: vi.fn() }));
vi.mock("@angee/ui", async (original) => ({ ...await original<typeof import("@angee/ui")>(),
  useChatter: () => ({ setInitialActiveTab: owner.initial, setCollapsed: owner.collapse, setActiveTab: owner.active }),
}));
vi.mock("@angee/timeline", () => ({ TIMELINE_TAB_ID: "timeline.rail" }));
const record = { model: "notes.Note", id: "nte_7" };
beforeEach(() => {
  vi.clearAllMocks(); owner.ids = ["first"]; owner.count = 1;
  owner.query.mockImplementation(() => ({ data: { record_timeline: { has_runs: true, open_decision_count: owner.count,
    records: [{ record_model: record.model, record_id: record.id, decisions: owner.ids.map((id) => ({ id, is_open: true, proposal: {} })) }] } } }));
});
afterEach(cleanup);

test("attention forces only a newly opened id on the same record, including a constant-count replacement", () => {
  const hook = renderHook(() => useRecordTimelineAttention(record));
  owner.initial.mockClear(); owner.collapse.mockClear();
  owner.count = 0; owner.ids = []; hook.rerender();
  expect(owner.initial).not.toHaveBeenCalled(); expect(owner.collapse).not.toHaveBeenCalled();
  owner.count = 1; owner.ids = ["second"]; hook.rerender();
  expect(owner.initial).toHaveBeenCalledExactlyOnceWith("timeline.rail", true);
  owner.initial.mockClear(); owner.collapse.mockClear(); hook.rerender();
  expect(owner.initial).not.toHaveBeenCalled();
  owner.ids = ["third"]; hook.rerender();
  expect(owner.initial).toHaveBeenCalledExactlyOnceWith("timeline.rail", true);
});

test("an unsaved record does not enable the eager attention read", () => {
  renderHook(() => useRecordTimelineAttention({ ...record, id: "" }));
  expect(owner.query.mock.lastCall?.[2].enabled).toBe(false);
});
