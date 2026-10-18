// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { baseIcons } from "../chrome/icon-registry";
import { AppRuntimeProvider, containersFromChildren, type ChatterViewContext } from "../runtime";
import { ACTIVITY_KINDS, ACTIVITY_PAGE_SIZE, Activity, type ActivityEntry, type ActivityEntrySource } from "./Activity";

afterEach(cleanup);

const context: ChatterViewContext = {
  pathname: "/notes/note_1",
  params: { id: "note_1" },
  route: { name: "notes.record", path: "/notes/$id", viewType: "record", modelLabel: "notes.Note" },
  view: { kind: "record", type: "record", sqid: "note_1" },
};

function renderActivity(source: ActivityEntrySource) {
  const containers = containersFromChildren([{ address: "record#activity", models: true }], {
    "record#activity": { "notes.entries": { content: source } },
  });
  return render(<AppRuntimeProvider runtime={{ containers, icons: baseIcons }}><Activity context={context} /></AppRuntimeProvider>);
}

describe("Activity", () => {
  test("merges every entry kind newest first and filters from one row", async () => {
    const entries = ACTIVITY_KINDS.map((kind, index): ActivityEntry => ({
      at: new Date(Date.UTC(2026, 9, 18, 12, 0, ACTIVITY_KINDS.length - index)),
      kind,
      actor: `Actor ${index}`,
      title: `${kind} entry`,
      body: `${kind} body`,
      reference: null,
    }));
    renderActivity({ kinds: ACTIVITY_KINDS, useEntries: () => entries });

    await waitFor(() => expect(screen.getByText("comment entry")).toBeTruthy());
    for (const kind of ACTIVITY_KINDS) expect(screen.getByText(`${kind} entry`)).toBeTruthy();
    const feedText = screen.getByRole("list", { name: "Activity" }).textContent ?? "";
    const positions = entries.map((entry) => feedText.indexOf(String(entry.title)));
    expect(positions).toEqual([...positions].sort((left, right) => left - right));

    fireEvent.click(screen.getByRole("button", { name: "Comments" }));
    expect(screen.getByText("comment entry")).toBeTruthy();
    expect(screen.queryByText("change entry")).toBeNull();
  });

  test("owns one global page and loads older entries", async () => {
    const entries = Array.from({ length: ACTIVITY_PAGE_SIZE + 1 }, (_, index): ActivityEntry => ({
      at: new Date(Date.UTC(2026, 9, 18, 12, 0, ACTIVITY_PAGE_SIZE - index)),
      kind: "change",
      actor: "System",
      title: `Change ${index + 1}`,
      body: null,
      reference: null,
    }));
    function useEntries(_record: unknown, window: { limit: number }): readonly ActivityEntry[] {
      return React.useMemo(() => entries.slice(0, window.limit), [window.limit]);
    }
    renderActivity({ kinds: ["change"], useEntries });

    await waitFor(() => expect(screen.getByText("Change 1")).toBeTruthy());
    expect(screen.queryByText(`Change ${ACTIVITY_PAGE_SIZE + 1}`)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Load older" }));
    await waitFor(() => expect(screen.getByText(`Change ${ACTIVITY_PAGE_SIZE + 1}`)).toBeTruthy());
  });

  test("shows the shared empty state once", async () => {
    const empty: readonly ActivityEntry[] = [];
    renderActivity({ kinds: ["comment"], useEntries: () => empty });
    await waitFor(() => expect(screen.getByText("No activity yet")).toBeTruthy());
    expect(screen.getAllByText("No activity yet")).toHaveLength(1);
  });

  test("does not rerun an owner hook just because its fresh result was aggregated", async () => {
    let calls = 0;
    renderActivity({
      kinds: ["comment"],
      useEntries: () => {
        calls += 1;
        return [{
          at: "2026-10-18T12:00:00Z",
          kind: "comment",
          actor: "Alex",
          title: "Fresh entry",
          body: null,
          reference: null,
        }];
      },
    });

    await waitFor(() => expect(screen.getByText("Fresh entry")).toBeTruthy());
    expect(calls).toBe(1);
  });
});
