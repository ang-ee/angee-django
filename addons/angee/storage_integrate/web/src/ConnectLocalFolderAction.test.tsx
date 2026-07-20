// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  connect: vi.fn(async () => ({ connect_local_folder: { id: "int_1" } })),
  mutationOptions: null as Record<string, unknown> | null,
  browseVariables: null as Record<string, unknown> | null,
}));

vi.mock("./documents", () => ({
  BrowseMountSource: "BrowseMountSource",
  ConnectLocalFolder: "ConnectLocalFolder",
  MOUNT_MODEL: "storage_integrate.Mount",
}));

vi.mock("@angee/refine", () => ({
  useAuthoredQuery: (
    _document: unknown,
    variables: Record<string, unknown>,
  ) => {
    actionMocks.browseVariables = variables;
    return {
      data: {
        browse_mount_source: {
          location: {
            token: "/srv/shared/docs",
            label: "docs",
            is_navigable: true,
            is_mountable: true,
            blocked_reason: "",
          },
          parent_token: "/srv/shared",
          entries: [],
          truncated: false,
          supports_manual_token: true,
        },
      },
      fetching: false,
      error: null,
    };
  },
  useAuthoredMutation: (_document: unknown, options?: Record<string, unknown>) => {
    actionMocks.mutationOptions = options ?? null;
    return [actionMocks.connect, { fetching: false, error: null }];
  },
}));

vi.mock("@angee/ui", () => ({
  Button: ({
    children,
    active: _active,
    loading: _loading,
    loadingText: _loadingText,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
    active?: boolean;
    loading?: boolean;
    loadingText?: string;
  }) => <button {...props}>{children}</button>,
  DialogForm: ({
    open,
    title,
    children,
    footer,
    onSubmit,
  }: {
    open: boolean;
    title: string;
    children: React.ReactNode;
    footer?: React.ReactNode;
    onSubmit?: React.FormEventHandler<HTMLFormElement>;
  }) =>
    open ? (
      <form aria-label={title} onSubmit={onSubmit}>
        {children}
        {footer}
      </form>
    ) : null,
  ErrorBanner: ({ description }: { description?: React.ReactNode }) =>
    description ? <p role="alert">{description}</p> : null,
  FieldLabel: ({ children, ...props }: React.HTMLAttributes<HTMLLabelElement>) => (
    <label {...props}>{children}</label>
  ),
  FieldRoot: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
  Select: ({
    options,
    onValueChange,
    ...props
  }: React.SelectHTMLAttributes<HTMLSelectElement> & {
    options: readonly { value: string; label: string }[];
    onValueChange: (value: string) => void;
  }) => (
    <select {...props} onChange={(event) => onValueChange(event.currentTarget.value)}>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
  Spinner: () => <span>spinner</span>,
  cn: (...values: unknown[]) => values.filter(Boolean).join(" "),
  errorMessage: (_error: unknown, fallback: string) => fallback,
  textRoleVariants: () => "",
}));

vi.mock("./i18n", () => ({
  useStorageIntegrateT: () => (key: string) => key,
}));

import { ConnectLocalFolderAction } from "./ConnectLocalFolderAction";

describe("ConnectLocalFolderAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.connect.mockClear();
    actionMocks.mutationOptions = null;
    actionMocks.browseVariables = null;
  });

  test("browses the local backend and submits its selected source token", async () => {
    render(<ConnectLocalFolderAction />);
    fireEvent.click(screen.getByRole("button", { name: "mount.connect.button" }));

    expect(actionMocks.browseVariables).toEqual({
      backendClass: "local_folder",
      credentialId: null,
      token: "",
    });
    expect(actionMocks.mutationOptions).toEqual({
      invalidateModels: ["storage_integrate.Mount"],
    });

    fireEvent.change(screen.getByLabelText("mount.connect.name"), {
      target: { value: " Shared " },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "mount.browse.useThisFolder" }),
    );
    fireEvent.submit(screen.getByRole("form", { name: "mount.connect.title" }));

    await waitFor(() =>
      expect(actionMocks.connect).toHaveBeenCalledWith({
        name: "Shared",
        path: "/srv/shared/docs",
        mode: "REFERENCE",
      }),
    );
  });
});
