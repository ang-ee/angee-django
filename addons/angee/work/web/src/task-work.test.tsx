// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("@angee/ui", () => ({
  Group: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  Field: () => null,
  RelativeTime: ({ value }: { value: unknown }) => <span>{String(value)}</span>,
}));
vi.mock("../i18n", () => ({ useWorkT: () => (key: string) => key }));
vi.mock("./i18n", () => ({ useWorkT: () => (key: string) => key }));
vi.mock("./estimates", () => ({ estimateLabel: () => "3" }));

import { WorkTaskCard, type WorkTaskRow } from "./task-work";

afterEach(cleanup);

const card = (task: WorkTaskRow) =>
  render(<WorkTaskCard task={task} estimateScale={undefined} />);

describe("board card footer", () => {
  test("answers is this mine, how urgent, by when", () => {
    // These three were on the list but not the card, so the board was the one
    // view of a task that could not answer them.
    card({
      id: "tsk_1",
      work_key: "ENG-8",
      title: "Draft signage",
      assignee: { id: "usr_1", display_name: "Ada Lovelace" },
      priority: "High",
      due_date: "2026-09-14",
    });

    expect(screen.getByLabelText("Ada Lovelace").textContent).toBe("AL");
    expect(screen.getByText("High")).toBeTruthy();
    expect(screen.getByText("2026-09-14")).toBeTruthy();
  });

  test("shows no empty slots for a task that has none of them", () => {
    const view = card({ id: "tsk_2", work_key: "ENG-9", title: "Unassigned" });
    expect(screen.getByText("ENG-9")).toBeTruthy();
    // No footer at all rather than a row of blanks.
    expect(view.container.querySelectorAll("span[aria-label]").length).toBe(0);
    expect(view.container.textContent).not.toContain("undefined");
  });

  test("never shows a relation's id as a name", () => {
    // A relation renders as its representation object; if only an id came back,
    // the card must show nothing rather than `usr_2`.
    const view = card({
      id: "tsk_3", work_key: "ENG-10", title: "Bare id", assignee: "usr_2",
    });
    // The id must not reach the screen as text *or* as the avatar's accessible
    // name -- checking only text content misses the label, and an id initialled
    // to "U" looks like a real person.
    expect(screen.queryByText(/usr_2/)).toBeNull();
    expect(screen.queryByLabelText(/usr_2/)).toBeNull();
    expect(view.container.querySelectorAll("span[aria-label]").length).toBe(0);
  });
});
