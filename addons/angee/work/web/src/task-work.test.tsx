// @vitest-environment happy-dom

import { ModelMetadataProvider } from "@angee/metadata";
import {
  testDataResource,
  testQueryField,
  testResourceQuery,
  withTestResourceInventory,
} from "@angee/metadata/testing";
import projects, { TASK_MODEL } from "@angee/projects";
import { AppRuntimeProvider, baseIcons } from "@angee/ui";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

vi.mock("@angee/ui", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/ui")>(),
  RelativeTime: ({ value }: { value: unknown }) => <span>{String(value)}</span>,
}));
vi.mock("./i18n", () => ({ useWorkT: () => (key: string) => key }));
vi.mock("./estimates", () => ({ estimateLabel: () => "3" }));

import { WorkTaskCard, type WorkTaskRow } from "./task-work";

afterEach(cleanup);

function card(task: WorkTaskRow, labelPath = "assignee.display_name") {
  const metadata = withTestResourceInventory({
    types: {
      TaskType: {
        fields: {
          assignee: { name: "assignee", kind: "relation", relationObject: true },
          priority: {
            name: "priority",
            kind: "enum",
            values: [
              { value: "NONE", description: "None" },
              { value: "HIGH", description: "High" },
              { value: "URGENT", description: "Urgent" },
            ],
          },
        },
        resource: testDataResource(TASK_MODEL, {
          query: testResourceQuery({
            fields: {
              assignee: testQueryField("assignee", {
                kind: "relation",
                relation: {
                  model: "iam.User",
                  identityPath: "assignee.id",
                  labelPath,
                },
              }),
            },
          }),
        }),
      },
    },
  });
  return render(
    <ModelMetadataProvider metadata={metadata}>
      <AppRuntimeProvider runtime={{ widgets: projects.widgets, icons: baseIcons }}>
        <WorkTaskCard task={task} estimateScale={undefined} />
      </AppRuntimeProvider>
    </ModelMetadataProvider>,
  );
}

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
    expect(screen.getByLabelText("Ada Lovelace").getAttribute("title")).toBe("Ada Lovelace");
    expect(screen.getByText("High")).toBeTruthy();
    expect(screen.getByText("2026-09-14")).toBeTruthy();
  });

  test("shows no empty slots for a task that has none of them", () => {
    const view = card({ id: "tsk_2", work_key: "ENG-9", title: "Unassigned" });
    expect(screen.getByText("ENG-9")).toBeTruthy();
    // No assignee avatar or missing-value text.
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

  test("uses shared avatar initials for a name with three words", () => {
    card({
      id: "tsk_initials",
      title: "Shared initials",
      assignee: { id: "usr_3", display_name: "Ada Lovelace Byron" },
    });

    expect(screen.getByLabelText("Ada Lovelace Byron").textContent).toBe("AL");
  });

  test("reads the declared nested representation", () => {
    card({
      id: "tsk_representation",
      title: "Metadata owns the label",
      assignee: {
        id: "usr_3",
        display_name: "Wrong display name",
        name: "Wrong name",
        title: "Wrong title",
        profile: { label: "Ada Lovelace Byron" },
      },
    }, "assignee.profile.label");

    const avatar = screen.getByLabelText("Ada Lovelace Byron");
    expect(avatar.textContent).toBe("AL");
    expect(avatar.getAttribute("title")).toBe("Ada Lovelace Byron");
    expect(screen.queryByLabelText(/Wrong/)).toBeNull();
  });
});

test("does not put NONE on a card as if it were a priority", () => {
  // Most seed tasks have priority NONE; rendering it pills nearly every card and
  // drowns the few that are actually URGENT.
  const view = card({
    id: "tsk_4",
    work_key: "ENG-11",
    title: "Unprioritised",
    priority: "NONE",
    assignee: { id: "usr_1", display_name: "Ada Lovelace" },
  });

  expect(screen.queryByText(/NONE/i)).toBeNull();
  expect(view.container.querySelector("svg")).toBeNull();
  // The rest of the footer still renders, so this omits a part rather than the row.
  expect(screen.getByLabelText("Ada Lovelace")).toBeTruthy();
  expect(view.container.textContent).toContain("ENG-11");
});

test("renders urgent through the registered priority cell", () => {
  const view = card({ id: "tsk_5", work_key: "ENG-12", title: "Escalate the custodian outage", priority: "URGENT" });
  // The metadata option label, as the list cells read it -- not the raw member.
  expect(screen.getByText("Urgent")).toBeTruthy();
  expect(screen.queryByText("URGENT")).toBeNull();
  expect(view.container.querySelector("svg.lucide-triangle-alert")).toBeTruthy();
});
