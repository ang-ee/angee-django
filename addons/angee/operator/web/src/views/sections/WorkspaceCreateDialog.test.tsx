// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

import { WorkspaceCreateDialog } from "./WorkspaceCreateDialog";

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  preflight: vi.fn(),
  create: vi.fn(),
}));

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => mocks.navigate,
}));

vi.mock("../../data/provision", () => ({
  toAnswerList: (inputs: Record<string, unknown>) =>
    Object.entries(inputs).map(([key, value]) => ({
      key,
      value: typeof value === "string" ? value : JSON.stringify(value),
    })),
  useWorkspacePreflight: () => ({
    run: mocks.preflight,
    result: { fetching: false, error: null },
  }),
  useWorkspaceCreate: () => ({
    run: mocks.create,
    result: { fetching: false, error: null },
  }),
}));

vi.mock("../../data/transport", () => ({
  useOperatorSnapshot: () => ({
    snapshot: {
      templates: [
        {
          id: "workspaces/dev",
          ref: "workspaces/dev",
          kind: "workspace",
          name: "dev",
          path: "/templates/workspaces/dev",
          inputs: [
            {
              name: "topic",
              type: "str",
              required: true,
              immutable: false,
              generated: false,
              default: null,
              question: true,
            },
            {
              name: "count",
              type: "int",
              required: false,
              immutable: false,
              generated: false,
              default: null,
              question: true,
            },
            {
              name: "internal_name",
              type: "str",
              required: true,
              immutable: true,
              generated: true,
              default: null,
              question: false,
            },
          ],
        },
        {
          id: "services/claude-code",
          ref: "services/claude-code",
          kind: "service",
          name: "claude-code",
          path: "/templates/services/claude-code",
          inputs: [],
        },
      ],
    },
    result: { fetching: false, error: null },
    refetch: vi.fn(),
  }),
}));

describe("WorkspaceCreateDialog", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
    Object.defineProperty(globalThis, "ResizeObserver", {
      configurable: true,
      value: class ResizeObserver {
        observe(): void {}
        unobserve(): void {}
        disconnect(): void {}
      },
    });
  });

  beforeEach(() => {
    mocks.navigate.mockReset();
    mocks.preflight.mockReset();
    mocks.create.mockReset();
  });

  afterEach(cleanup);

  test("lists workspace templates and surfaces preflight failures on template inputs", async () => {
    mocks.preflight.mockResolvedValue({
      workspaceCreatePreflight: {
        ok: false,
        template: "workspaces/dev",
        resolvedTemplate: "workspaces/dev",
        effectiveInputs: [],
        missingRequired: ["topic"],
        invalidInputs: [{ field: "count", reason: "not an integer: abc" }],
      },
    });

    renderDialog();
    await chooseTemplate("dev");

    expect(screen.getByLabelText("topic")).toBeTruthy();
    expect(screen.getByLabelText("count")).toBeTruthy();
    expect(screen.queryByLabelText("internal_name")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }));

    expect(await screen.findByText("This input is required.")).toBeTruthy();
    expect(screen.getByText("not an integer: abc")).toBeTruthy();
    expect(mocks.create).not.toHaveBeenCalled();
  });

  test("creates with preflighted answers and navigates to the workspace detail", async () => {
    mocks.preflight.mockResolvedValue({
      workspaceCreatePreflight: {
        ok: true,
        template: "workspaces/dev",
        resolvedTemplate: "workspaces/dev",
        effectiveInputs: [{ key: "topic", value: "slice-5" }],
        missingRequired: [],
        invalidInputs: [],
      },
    });
    mocks.create.mockResolvedValue({
      insert_workspaces_one: { id: "slice-5", name: "slice-5" },
    });

    renderDialog();
    await chooseTemplate("dev");
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "slice-5" },
    });
    fireEvent.change(screen.getByLabelText("topic"), {
      target: { value: "slice-5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create workspace" }));

    await waitFor(() =>
      expect(mocks.preflight).toHaveBeenCalledWith({
        input: {
          template: "workspaces/dev",
          name: "slice-5",
          inputs: [
            { key: "topic", value: "slice-5" },
          ],
        },
      }),
    );
    expect(mocks.create).toHaveBeenCalledWith({
      object: {
        template: "workspaces/dev",
        name: "slice-5",
        inputs: [{ key: "topic", value: "slice-5" }],
      },
    });
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: "/operator/workspaces/slice-5",
    });
  });
});

function renderDialog(): ReturnType<typeof render> {
  return render(
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <WorkspaceCreateDialog open onOpenChange={vi.fn()} />
    </AppRuntimeProvider>,
  );
}

async function chooseTemplate(name: string): Promise<void> {
  fireEvent.click(
    screen.getByRole("combobox", { name: "Workspace template" }),
  );
  const option = await screen.findByRole("option", { name });
  expect(screen.queryByRole("option", { name: "claude-code" })).toBeNull();
  fireEvent.click(option);
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Workspace template" }).textContent,
    ).toContain(name),
  );
}
