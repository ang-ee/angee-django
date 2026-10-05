// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
import { TimelineStory, type TimelineRequest } from "./RecordTimeline.stories";

beforeAll(() => {
  Element.prototype.getAnimations ??= () => [];
  Element.prototype.scrollIntoView ??= vi.fn();
});
afterEach(() => cleanup());

test("a completed run shows the current record state, workflow name, local time and run link", async () => {
  render(<TimelineStory state="clean" recordState={{ label: "Posted", tone: "success" }} />);
  expect(await screen.findByText("Posted")).toBeTruthy();
  expect(screen.queryByText("Plan complete")).toBeNull();
  expect(screen.getByRole("heading", { name: /Record review/ })).toBeTruthy();
  expect(screen.getByText("Open run")).toBeTruthy();
  expect(document.querySelector('time[datetime="2026-10-03T10:00:00Z"]')).toBeTruthy();
});

test.each([
  ["decision", "Waiting for decisions"], ["clean", "Plan complete"], ["error", "Stopped on an error"],
  ["run", "Waiting for another run"], ["stopped", "Withdrawn: stopped by River"],
] as const)("renders the %s timeline state", async (state, label) => {
  render(<TimelineStory state={state} />);
  expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
  if (state === "error") expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  if (state === "run") expect(screen.getByRole("button", { name: /Related record check/ })).toBeTruthy();
});

test("answers inline with the observed revision and refreshes the stepper", async () => {
  const requests: TimelineRequest[] = [];
  render(<TimelineStory onRequest={(request) => requests.push(request)} />);
  fireEvent.click(await screen.findByRole("radio", { name: /Use proposed name/ }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  await screen.findByText("Plan complete");
  expect(requests.find(({ query }) => query.includes("decide("))?.variables).toMatchObject({
    id: "dcn_review", revision: 3, chosen: ["accept"],
  });
  expect(screen.queryByRole("radio")).toBeNull();
  expect(screen.getByText("Chose: Use proposed name")).toBeTruthy();
});

test("only records with runs select the timeline initially", async () => {
  const view = render(<TimelineStory state="empty" />);
  await waitFor(() => expect(screen.getByTestId("timeline-right").getAttribute("data-state")).toBe("false:messaging.comments"));
  view.unmount();
  render(<TimelineStory />);
  await screen.findByText("Started manually by River");
  expect((await screen.findByTestId("timeline-right")).getAttribute("data-state")).toBe("false:workflows.timeline");
});

test("a first visit opens a collapsed pane only when a decision is waiting", async () => {
  const view = render(<TimelineStory collapsed />);
  await waitFor(() => expect(screen.getByTestId("timeline-right").getAttribute("data-state")).toBe("false:workflows.timeline"));
  view.unmount();
  render(<TimelineStory collapsed state="clean" />);
  await waitFor(() => expect(screen.getByTestId("timeline-right").getAttribute("data-state")).toBe("true:workflows.timeline"));
});

test("answered cards do not nest list items outside a list", async () => {
  const { container } = render(<TimelineStory state="clean" />);
  await screen.findByText("Chose: Use proposed name");
  for (const item of container.querySelectorAll("li")) {
    expect(["UL", "OL"]).toContain(item.parentElement?.tagName);
  }
});

test("a retry with possible duplicate effects uses the shared acknowledgement form", async () => {
  const requests: TimelineRequest[] = [];
  render(<TimelineStory state="error" duplicateRisk onRequest={(request) => requests.push(request)} />);
  const label = "Retry accepting a possible duplicate";
  fireEvent.click(await screen.findByRole("button", { name: label }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: label }));
  const checkbox = within(dialog).getByRole("checkbox", { name: "I understand this retry may repeat an external effect." });
  await waitFor(() => expect(checkbox.getAttribute("aria-invalid")).toBe("true"));
  expect(requests.some(({ query }) => query.includes("retry_step_accepting_duplicate("))).toBe(false);
  fireEvent.click(checkbox);
  fireEvent.click(within(dialog).getByRole("button", { name: label }));
  await screen.findByText("Waiting for decisions");
  expect(requests.find(({ query }) => query.includes("retry_step_accepting_duplicate("))?.variables.id).toBe("wsr_review");
});

test("stop withdraws the card, removes future steps and refreshes the pane", async () => {
  const requests: TimelineRequest[] = [];
  render(<TimelineStory onRequest={(request) => requests.push(request)} />);
  const card = await screen.findByRole("region", { name: "Confirm the name" });
  fireEvent.click(within(card).getByRole("button", { name: "Stop and do it manually" }));
  await screen.findByText("Withdrawn: stopped by River");
  expect(requests.find(({ query }) => query.includes("cancel_workflow_run("))?.variables.id).toBe("wfr_review");
  expect(screen.queryByText("May also: Check a related record")).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop and do it manually" })).toBeNull();
});

test("routine history folds while waiting and the record reference opens a peek", async () => {
  render(<TimelineStory />);
  const fold = await screen.findByRole("button", { name: "Show 7 steps done" });
  fireEvent.click(fold);
  expect(screen.getByText("Receive record")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Review notes/ }));
  expect(await screen.findByRole("navigation", { name: "Related records" })).toBeTruthy();
  expect(screen.getByTestId("timeline-right").getAttribute("data-state")).toBe("false:records");
});

test("form marks reveal and highlight the inline card through the right host", async () => {
  render(<TimelineStory />);
  await screen.findByRole("button", { name: "Unconfirmed: Name" });
  expect(screen.getByTestId("timeline-right").getAttribute("data-state")).toBe("false:workflows.timeline");
  expect(screen.getByRole("region", { name: "Confirm the name" }).className).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Toggle timeline" }));
  expect(screen.queryByRole("region", { name: "Confirm the name" })).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: "Unconfirmed: Name" }));
  await waitFor(() => expect(document.querySelector('[data-decision="dcn_review"]')?.className).toContain("ring-2"));
});

test("one component publishes to the left host", async () => {
  render(<TimelineStory side="left" />);
  await screen.findByText("Waiting for decisions");
  expect(screen.getByTestId("timeline-left").textContent).toContain("Confirm the name");
  expect(screen.queryByTestId("timeline-right")).toBeNull();
});

test("selection groups open questions and held runs by record or question", async () => {
  render(<TimelineStory state="set" />);
  await screen.findByText("2 records · 1 open decision · 1 run waiting or stopped");
  expect(screen.getAllByRole("region", { name: "Confirm the name" })).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "By question" }));
  expect(screen.getAllByRole("region", { name: "Confirm the name" })).toHaveLength(1);
  expect(screen.getAllByText("Confirm the name").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
});
