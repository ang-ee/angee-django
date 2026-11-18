// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  connect: vi.fn(async () => ({ connect_mount: { id: "int_1" } })),
  dialogProps: null as Record<string, unknown> | null,
  requiresCredential: true,
}));

vi.mock("./documents", () => ({
  BrowseMountSource: "BrowseMountSource",
  ConnectMount: "ConnectMount",
  MountBackendFacts: "MountBackendFacts",
  MountCopyTargets: "MountCopyTargets",
  MOUNT_MODEL: "storage_integrate.Mount",
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...original,
    useAuthoredQuery: (document: unknown) => ({
      data: document === "MountBackendFacts"
        ? {
          mount_backend: {
            key: "remote_source",
            requires_credential: actionMocks.requiresCredential,
            oauth_client: "clt_1",
            accounts: [{ id: "crd_1", display_name: "ada@example.com" }],
          },
        }
        : {
          backends: [
            { id: "bkd_2", label: "Archive", writable: true, is_archived: false },
            { id: "bkd_3", label: "Mounted (external)", writable: false, is_archived: false },
            { id: "bkd_4", label: "Retired", writable: true, is_archived: true },
          ],
        },
    }),
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
        root: "folder-1",
        mode: "COPY",
        copyBackendId: "bkd_2",
      },
      submitLabel: "submit",
    }),
  };
});

import { ConnectMountAction, type MountConnectVariables } from "./ConnectMountAction";

type FieldProbe = {
  name: string;
  options?: readonly { value: string }[];
  showWhen?: (values: Record<string, unknown>) => boolean;
};

function dialogFields(): readonly FieldProbe[] {
  return (actionMocks.dialogProps?.fields ?? []) as readonly FieldProbe[];
}

describe("ConnectMountAction", () => {
  beforeEach(() => {
    actionMocks.connect.mockClear();
    actionMocks.dialogProps = null;
    actionMocks.requiresCredential = true;
  });

  test("asks a credential backend for the account and a copy mount for a writable target, and submits both", async () => {
    render(<ConnectMountAction backendClass="remote_source" i18nPrefix="remote.mount.connect" />);
    fireEvent.click(screen.getByRole("button", { name: "remote.mount.connect.button" }));

    expect(dialogFields().map((field) => field.name)).toEqual(["name", "credentialId", "root", "mode", "copyBackendId"]);
    const copyTarget = dialogFields().find((field) => field.name === "copyBackendId");
    expect(copyTarget?.options?.map((option) => option.value)).toEqual(["bkd_2"]);
    expect(copyTarget?.showWhen?.({ mode: "COPY" })).toBe(true);
    expect(copyTarget?.showWhen?.({ mode: "REFERENCE" })).toBe(false);

    fireEvent.submit(screen.getByRole("form", { name: "remote.mount.connect.title" }));
    await waitFor(() =>
      expect(actionMocks.connect).toHaveBeenCalledWith({
        backendClass: "remote_source",
        name: "Team",
        credentialId: "crd_1",
        root: "folder-1",
        mode: "COPY",
        copyBackendId: "bkd_2",
      } satisfies MountConnectVariables, { errorNotification: false }),
    );
  });

  test("a backend reading without an account asks for none", () => {
    actionMocks.requiresCredential = false;
    render(<ConnectMountAction backendClass="local_folder" i18nPrefix="mount.localFolder" />);
    fireEvent.click(screen.getByRole("button", { name: "Connect local folder" }));
    expect(dialogFields().map((field) => field.name)).toEqual(["name", "root", "mode", "copyBackendId"]);
  });
});
