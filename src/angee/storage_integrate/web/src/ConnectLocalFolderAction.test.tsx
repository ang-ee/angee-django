// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  connect: vi.fn(async () => ({ connect_local_folder: { id: "int_1" } })),
  dialogProps: null as Record<string, unknown> | null,
  mutationOptions: null as Record<string, unknown> | null,
}));

vi.mock("./documents", () => ({
  BrowseMountSource: "BrowseMountSource",
  ConnectLocalFolder: "ConnectLocalFolder",
  MOUNT_MODEL: "storage_integrate.Mount",
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...original,
    useAuthoredQuery: vi.fn(),
    useAuthoredMutation: (_document: unknown, options?: Record<string, unknown>) => {
      actionMocks.mutationOptions = options ?? null;
      return [actionMocks.connect, { fetching: false, error: null }];
    },
  };
});

vi.mock("@angee/ui", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/ui")>();
  const { createMutationDialogTestDouble } = await import("@angee/ui/testing");
  return {
    ...original,
    Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
      <button {...props}>{children}</button>
    ),
    Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
    MutationDialog: createMutationDialogTestDouble({
      capture: (props) => {
        actionMocks.dialogProps = props;
      },
      values: {
        name: "  Shared  ",
        path: "/srv/shared/docs",
        mode: "REFERENCE",
      },
      submitLabel: "submit",
    }),
  };
});

import { ConnectLocalFolderAction } from "./ConnectLocalFolderAction";

describe("ConnectLocalFolderAction", () => {
  beforeEach(() => {
    actionMocks.connect.mockClear();
    actionMocks.dialogProps = null;
    actionMocks.mutationOptions = null;
  });

  test("declares the local backend and submits typed mount variables", async () => {
    render(<ConnectLocalFolderAction />);
    fireEvent.click(screen.getByRole("button", { name: "Connect local folder" }));

    expect(actionMocks.mutationOptions).toEqual({
      invalidateModels: ["storage_integrate.Mount"],
    });
    expect(actionMocks.dialogProps).toMatchObject({
      title: "Connect local folder",
      submitLabel: "Connect",
      submittingLabel: "Connecting…",
      errorFallback: "Could not connect the local folder.",
      size: "lg",
    });

    fireEvent.submit(screen.getByRole("form", { name: "Connect local folder" }));
    await waitFor(() =>
      expect(actionMocks.connect).toHaveBeenCalledWith({
        name: "Shared",
        path: "/srv/shared/docs",
        mode: "REFERENCE",
      }),
    );
  });
});
