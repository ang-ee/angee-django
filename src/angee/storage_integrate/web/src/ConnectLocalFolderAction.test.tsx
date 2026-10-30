// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

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

vi.mock("./i18n", () => ({
  useStorageIntegrateT: () => (key: string) =>
    key === "mount.localFolder.error"
      ? "Could not connect the local folder."
      : key,
}));

import { ConnectLocalFolderAction } from "./ConnectLocalFolderAction";

describe("ConnectLocalFolderAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.connect.mockClear();
    actionMocks.dialogProps = null;
    actionMocks.mutationOptions = null;
  });

  test("declares the local backend and submits typed mount variables", async () => {
    render(<ConnectLocalFolderAction />);
    fireEvent.click(screen.getByRole("button", { name: "mount.localFolder.button" }));

    expect(actionMocks.mutationOptions).toEqual({
      invalidateModels: ["storage_integrate.Mount"],
    });
    expect(actionMocks.dialogProps).toMatchObject({
      title: "mount.localFolder.title",
      submitLabel: "mount.connect.submit",
      submittingLabel: "mount.connect.submitting",
      errorFallback: "Could not connect the local folder.",
      size: "lg",
    });

    fireEvent.submit(screen.getByRole("form", { name: "mount.localFolder.title" }));
    await waitFor(() =>
      expect(actionMocks.connect).toHaveBeenCalledWith({
        name: "Shared",
        path: "/srv/shared/docs",
        mode: "REFERENCE",
      }),
    );
  });
});
