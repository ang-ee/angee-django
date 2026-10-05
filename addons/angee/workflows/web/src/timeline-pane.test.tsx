// @vitest-environment happy-dom
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useRecordTimelineAttention, useRecordTimelinePane } from "./timeline-pane";

const owner = vi.hoisted(() => ({
  passive: false, query: vi.fn(), publish: vi.fn(), primary: vi.fn(), initial: vi.fn(), collapse: vi.fn(), active: vi.fn(),
  ids: ["first"], count: 1,
}));
vi.mock("@angee/refine", async (original) => ({ ...await original<typeof import("@angee/refine")>(), useAuthoredQuery: owner.query }));
vi.mock("@angee/decisions", async (original) => ({ ...await original<typeof import("@angee/decisions")>(), useDecisionFieldMarks: vi.fn() }));
vi.mock("@angee/ui", async (original) => ({ ...await original<typeof import("@angee/ui")>(),
  useRecordPeekContext: () => owner.passive ? {} : null,
  useChatterContent: owner.publish, usePrimaryPane: owner.primary,
  useChatter: () => ({ setInitialActiveTab: owner.initial, setCollapsed: owner.collapse, setActiveTab: owner.active }),
}));
const record = { model: "notes.Note", id: "nte_7" };
beforeEach(() => {
  vi.clearAllMocks(); owner.passive = false; owner.ids = ["first"]; owner.count = 1;
  owner.query.mockImplementation(() => ({ data: { record_timeline: { has_runs: true, open_decision_count: owner.count,
    records: [{ record_model: record.model, record_id: record.id, decisions: owner.ids.map((id) => ({ id, is_open: true, proposal: {} })) }] } } }));
});
afterEach(cleanup);

test("a passive peek publishes no pane and disables its read", () => {
  owner.passive = true;
  renderHook(() => useRecordTimelinePane({ record, side: "right" }));
  expect(owner.publish).toHaveBeenCalledWith(null);
  expect(owner.primary).toHaveBeenCalledWith(null);
  expect(owner.query.mock.lastCall?.[2].enabled).toBe(false);
  expect(owner.initial).not.toHaveBeenCalled();
});

test("attention forces only a newly opened id on the same record, including a constant-count replacement", () => {
  const hook = renderHook(() => useRecordTimelineAttention(record));
  owner.initial.mockClear(); owner.collapse.mockClear();
  owner.count = 0; owner.ids = []; hook.rerender();
  expect(owner.initial).not.toHaveBeenCalled(); expect(owner.collapse).not.toHaveBeenCalled();
  owner.count = 1; owner.ids = ["second"]; hook.rerender();
  expect(owner.initial).toHaveBeenCalledExactlyOnceWith("workflows.timeline", true);
  owner.initial.mockClear(); owner.collapse.mockClear(); hook.rerender();
  expect(owner.initial).not.toHaveBeenCalled();
  owner.ids = ["third"]; hook.rerender();
  expect(owner.initial).toHaveBeenCalledExactlyOnceWith("workflows.timeline", true);
});

test("an unsaved record does not enable the eager timeline read", () => {
  renderHook(() => useRecordTimelinePane({ record: { ...record, id: "" }, side: "right" }));
  expect(owner.query.mock.lastCall?.[2].enabled).toBe(false);
});
