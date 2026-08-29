// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listProps: null as Record<string, unknown> | null,
  add: vi.fn(),
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/ui")>();
  const { createMutationDialogTestDouble } = await import("@angee/ui/testing");
  return {
  ...original,
  Button: ({
    children,
    onClick,
    type,
  }: {
    children?: React.ReactNode;
    onClick?: React.MouseEventHandler<HTMLButtonElement>;
    type?: "button" | "submit";
  }) => <button type={type} onClick={onClick}>{children}</button>,
  Glyph: () => null,
  ListView: (props: Record<string, unknown>) => {
    mocks.listProps = props;
    return <>{props.toolbarActions as React.ReactNode}</>;
  },
  MutationDialog: createMutationDialogTestDouble({
    values: { circle: "circle-2" },
    submitLabel: "Submit membership",
  }),
  defineRowAction: (declaration: Record<string, unknown>) => declaration,
  rowIdVariables: (row: { id: string }) => ({ id: row.id }),
  useAuthoredResourceMutation: () => [mocks.add, { fetching: false, error: null }],
  };
});

vi.mock("./i18n", () => ({
  usePartiesT: () => (key: string) => key,
}));

import { PersonCirclesList } from "./CircleMembershipList";

describe("CircleMembershipList", () => {
  beforeEach(() => {
    mocks.listProps = null;
    mocks.add.mockReset();
  });

  test("keeps add behavior and declares removal through the shared row verb", async () => {
    render(<PersonCirclesList personId="person-1" />);

    expect(mocks.listProps).toMatchObject({
      resource: "parties.CircleMember",
      scope: "local",
      baseFilter: { party: { exact: "person-1" } },
    });
    const columns = mocks.listProps?.columns as Array<{ field: string }>;
    expect(columns.map((column) => column.field)).toEqual([
      "circle.name",
      "source",
      "confidence",
    ]);
    const [remove] = mocks.listProps?.rowActions as Array<{
      kind: string;
      variables: (row: { id: string }) => unknown;
      pendingPolicy: string;
      icon: string;
    }>;
    expect(remove?.variables({ id: "membership-1" })).toEqual({ id: "membership-1" });
    expect(remove).toMatchObject({
      kind: "authored",
      pendingPolicy: "disable-actions",
      icon: "trash",
    });

    fireEvent.click(screen.getByRole("button", { name: "circle.membership.addCircle" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit membership" }));
    await waitFor(() =>
      expect(mocks.add).toHaveBeenCalledWith({
        circle: "circle-2",
        party: "person-1",
      }),
    );
  });
});
