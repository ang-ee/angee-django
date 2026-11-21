// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRuntimeProvider, ToastProvider, createRouteHref, defaultWidgets } from "@angee/ui";
import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

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
          id: "workspaces/review",
          ref: "workspaces/review",
          kind: "workspace",
          name: "review",
          path: "/templates/workspaces/review",
          inputs: [
            {
              name: "topic",
              type: "str",
              required: false,
              immutable: false,
              generated: false,
              default: "triage",
              question: true,
            },
            {
              name: "draft",
              type: "bool",
              required: false,
              immutable: false,
              generated: false,
              default: "yes",
              question: true,
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

  test("lists workspace templates and surfaces preflight failures on template inputs", async () => {
    mocks.preflight.mockResolvedValue({
      workspaceCreatePreflight: {
        ok: false,
        template: "workspaces/dev",
        resolvedTemplate: "workspaces/dev",
        effectiveInputs: [],
        missingRequired: [],
        invalidInputs: [{ field: "count", reason: "not an integer: abc" }],
      },
    });

    renderDialog();
    await chooseTemplate("dev");

    expect(screen.getByLabelText("topic")).toBeTruthy();
    expect(screen.getByLabelText("count")).toBeTruthy();
    expect(screen.queryByLabelText("internal_name")).toBeNull();

    const submit = screen.getByRole("button", { name: "Create workspace" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("topic"), {
      target: { value: "provided" },
    });
    fireEvent.change(screen.getByLabelText("count"), {
      target: { value: "abc" },
    });
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);

    expect(await screen.findByText("not an integer: abc")).toBeTruthy();
    expect(mocks.create).not.toHaveBeenCalled();
  });

  test("creates with preflighted answers, both daemon calls quiet, and navigates to the workspace detail", async () => {
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
    const submit = screen.getByRole("button", { name: "Create workspace" }) as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);

    // The dialog renders a failure of either call itself, so each runs with refine's toast off.
    await waitFor(() =>
      expect(mocks.preflight).toHaveBeenCalledWith({
        input: {
          template: "workspaces/dev",
          name: "slice-5",
          inputs: [
            { key: "topic", value: "slice-5" },
          ],
        },
      }, { errorNotification: false }),
    );
    expect(mocks.create).toHaveBeenCalledWith({
      object: {
        template: "workspaces/dev",
        name: "slice-5",
        inputs: [{ key: "topic", value: "slice-5" }],
      },
    }, { errorNotification: false });
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: "/operator/workspaces/slice-5",
    });
  });

  test("a template's preset replaces every input it names, typed ones too, and the dialog submits the seeds", async () => {
    mocks.preflight.mockResolvedValue({
      workspaceCreatePreflight: {
        ok: false,
        template: "workspaces/review",
        resolvedTemplate: "workspaces/review",
        effectiveInputs: [],
        missingRequired: [],
        invalidInputs: [],
      },
    });

    renderDialog();
    await chooseTemplate("dev");
    // Each input of the chosen template takes its initial value, validated as it lands.
    expect((screen.getByLabelText("topic") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("count") as HTMLInputElement).value).toBe("");
    expect(await screen.findByText("This field is required.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("topic"), { target: { value: "typed" } });
    fireEvent.change(screen.getByLabelText("count"), { target: { value: "3" } });

    await chooseTemplate("review");
    expect((screen.getByLabelText("topic") as HTMLInputElement).value).toBe("triage");
    expect(screen.getByRole("switch", { name: "draft" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByLabelText("count")).toBeNull();

    await chooseTemplate("dev");
    expect((screen.getByLabelText("topic") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("count") as HTMLInputElement).value).toBe("");
    expect(screen.queryByRole("switch", { name: "draft" })).toBeNull();

    await chooseTemplate("review");
    const submit = screen.getByRole("button", { name: "Create workspace" }) as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);
    await waitFor(() =>
      expect(mocks.preflight).toHaveBeenCalledWith({
        input: {
          template: "workspaces/review",
          inputs: [
            { key: "topic", value: "triage" },
            { key: "draft", value: "true" },
          ],
        },
      }, { errorNotification: false }),
    );
    expect(mocks.create).not.toHaveBeenCalled();
  });
});

function renderDialog(): ReturnType<typeof render> {
  return render(
    <AppRuntimeProvider runtime={{ widgets: defaultWidgets, routeHref: createRouteHref([{ name: "operator.workspaces.detail", path: "/operator/workspaces/$name" }]) }}>
      <WorkspaceCreateDialog open onOpenChange={vi.fn()} />
    </AppRuntimeProvider>,
    { wrapper: ToastProvider },
  );
}

async function chooseTemplate(name: string): Promise<void> {
  fireEvent.click(
    screen.getByRole("combobox", { name: "Workspace template" }),
  );
  const option = await screen.findByRole("option", { name });
  expect(screen.queryByRole("option", { name: "claude-code" })).toBeNull();
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Workspace template" }).textContent,
    ).toContain(name),
  );
}
