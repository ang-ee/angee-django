// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { createRouteHref } from "@angee/ui/runtime";
import workflows from "../../../workflows/web/src/index";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  routeHref: vi.fn(),
  start: vi.fn(),
  toast: { danger: vi.fn() },
}));

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => mocks.navigate,
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({
    data: {
      workflows_for_subject_declaration: [
        { id: "workflow-1", name: "Deduplicate contacts" },
      ],
    },
  }),
  useAuthoredMutation: () => [mocks.start, { fetching: false }],
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  useRouteHref: () => mocks.routeHref,
  useToast: () => mocks.toast,
}));

import { RunDedupeAction } from "./RunDedupeAction";

beforeEach(() => {
  mocks.navigate.mockReset();
  mocks.routeHref.mockReset();
  const routeHref = createRouteHref(workflows.routes ?? []);
  mocks.routeHref.mockImplementation(routeHref);
  Object.assign(mocks.routeHref, { maybe: routeHref.maybe });
  mocks.start.mockReset();
  mocks.start.mockResolvedValue({
    start_workflow_run: { ok: true, message: "Started", id: "workflow-run-1" },
  });
  mocks.toast.danger.mockReset();
});

afterEach(cleanup);

describe("RunDedupeAction", () => {
  test("navigates to the composed workflow run route without changing display-name selection", async () => {
    render(<RunDedupeAction />);

    fireEvent.click(screen.getByRole("button", { name: "Run dedupe" }));

    await waitFor(() => {
      expect(mocks.navigate).toHaveBeenCalledWith({
        to: "/workflows/runs/workflow-run-1",
      });
    });
  });

  test("disables without throwing when the workflows addon is not composed", () => {
    const routeHref = createRouteHref([]);
    mocks.routeHref.mockImplementation(routeHref);
    Object.assign(mocks.routeHref, { maybe: routeHref.maybe });

    render(<RunDedupeAction />);

    const button = screen.getByRole("button", { name: "Run dedupe" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(mocks.start).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });
});
