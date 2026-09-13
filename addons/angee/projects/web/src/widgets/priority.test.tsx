// @vitest-environment happy-dom

import { AppRuntimeProvider, RowsListView, defaultWidgets } from "@angee/ui";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import projects from "../index";

afterEach(cleanup);

test("task list cells show urgent's glyph and leave none empty", async () => {
  render(
    <AppRuntimeProvider runtime={{ widgets: { ...defaultWidgets, ...projects.widgets } }}>
      <RowsListView
        presentation="embedded"
        rows={[
          { id: "tsk_urgent", title: "Fix outage", priority: "URGENT" },
          { id: "tsk_none", title: "Review notes", priority: "NONE" },
        ]}
        columns={[
          { field: "title", header: "Task" },
          {
            field: "priority",
            header: "Priority",
            widget: "angee.projects.priority",
            options: [
              { value: "URGENT", label: "Urgent" },
              { value: "NONE", label: "None" },
            ],
          },
        ]}
      />
    </AppRuntimeProvider>,
  );

  const urgent = await screen.findByRole("row", { name: /Fix outage/ });
  const urgentCell = within(urgent).getAllByRole("cell")[1]!;
  expect(urgentCell.textContent).toBe("Urgent");
  expect(urgentCell.querySelector("svg.lucide-triangle-alert")).toBeTruthy();

  const none = screen.getByRole("row", { name: /Review notes/ });
  const noneCell = within(none).getAllByRole("cell")[1]!;
  expect(noneCell.textContent).toBe("");
  expect(noneCell.querySelector("svg")).toBeNull();
});
