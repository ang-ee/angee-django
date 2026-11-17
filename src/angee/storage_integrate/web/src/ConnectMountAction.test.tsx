// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  connect: vi.fn(async () => ({ connect_google_drive: { id: "int_1" } })),
  dialogProps: null as Record<string, unknown> | null,
}));

vi.mock("./documents", () => ({
  BrowseMountSource: "BrowseMountSource",
  MountAccounts: "MountAccounts",
  MOUNT_MODEL: "storage_integrate.Mount",
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...original,
    useAuthoredQuery: vi.fn(),
    useAuthoredMutation: () => [actionMocks.connect, { fetching: false, error: null }],
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
        name: "Team",
        credentialId: "crd_1",
        path: "0ATeam",
        mode: "COPY",
        copyBackendId: "bkd_2",
      },
      submitLabel: "submit",
    }),
  };
});

import { ConnectMountAction, type MountConnectVariables } from "./ConnectMountAction";

describe("ConnectMountAction", () => {
  beforeEach(() => {
    actionMocks.connect.mockClear();
    actionMocks.dialogProps = null;
  });

  test("asks an account backend for the account and a copy mount for its target, and submits both", async () => {
    render(
      <ConnectMountAction
        mutationDocument={"ConnectGoogleDrive" as never}
        backendClass="google_drive"
        i18nPrefix="gdrive.mount.connect"
        invalidateModel="storage_integrate.Mount"
        requiresAccount
        choosesCopyBackend
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "gdrive.mount.connect.button" }));

    const fields = (actionMocks.dialogProps?.fields ?? []) as readonly { name: string; showWhen?: (values: Record<string, unknown>) => boolean }[];
    expect(fields.map((field) => field.name)).toEqual(["name", "credentialId", "path", "mode", "copyBackendId"]);
    const copyTarget = fields.find((field) => field.name === "copyBackendId");
    expect(copyTarget?.showWhen?.({ mode: "COPY" })).toBe(true);
    expect(copyTarget?.showWhen?.({ mode: "REFERENCE" })).toBe(false);

    fireEvent.submit(screen.getByRole("form", { name: "gdrive.mount.connect.title" }));
    await waitFor(() =>
      expect(actionMocks.connect).toHaveBeenCalledWith({
        name: "Team",
        credentialId: "crd_1",
        path: "0ATeam",
        mode: "COPY",
        copyBackendId: "bkd_2",
      } satisfies MountConnectVariables),
    );
  });

  test("a path backend asks for neither", () => {
    render(
      <ConnectMountAction
        mutationDocument={"ConnectLocalFolder" as never}
        backendClass="local_folder"
        i18nPrefix="mount.localFolder"
        invalidateModel="storage_integrate.Mount"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Connect local folder" }));
    const fields = (actionMocks.dialogProps?.fields ?? []) as readonly { name: string }[];
    expect(fields.map((field) => field.name)).toEqual(["name", "path", "mode"]);
  });
});
