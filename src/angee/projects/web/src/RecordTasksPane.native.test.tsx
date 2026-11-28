// @vitest-environment happy-dom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";
import type { GraphQLWireRequest } from "@angee/storybook/testing";

import { RecordTasksStory } from "./RecordTasksPane.stories";

// The pane's own tests own where each reason lands and where focus goes; this one proves the
// story's fixture answers through the real pane. The browser pass reads focus.
test("the story's schedule, refused under its server's title, says the reason at the pane's text", async () => {
  const requests: GraphQLWireRequest[] = [];
  render(<RecordTasksStory refuse="text" onRequest={(request) => requests.push(request)} />);
  const text = await screen.findByRole("textbox", { name: "What needs doing" });
  fireEvent.change(text, { target: { value: "Confirm the delivery address" } });
  fireEvent.submit(text.closest("form")!);
  await waitFor(() => expect(text.getAttribute("aria-invalid")).toBe("true"));
  expect(requests.find(({ fields }) => fields.includes("schedule_record_task"))?.variables)
    .toMatchObject({ title: "Confirm the delivery address" });
  // Base UI ties a field control's reason in an effect of its own: wait for it.
  const reason = await waitFor(() => {
    const described = (text.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean)
      .map((id) => document.getElementById(id));
    const found = described.find((node) => node?.textContent?.includes("A record task needs a title."));
    expect(found).toBeTruthy();
    return found!;
  });
  expect(reason.closest("[role='alert']")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});
