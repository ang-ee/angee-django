// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode, Ref } from "react";
import type { RecordPanelContext } from "@angee/ui";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  add: vi.fn(),
  listProps: [] as Record<string, unknown>[],
  mutationProps: null as Record<string, unknown> | null,
  queryData: undefined as unknown,
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: () => ({ data: mocks.queryData, isFetching: false, error: null }),
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  Button: ({ children }: { children?: ReactNode }) => <button type="button">{children}</button>,
  Code: ({ children }: { children?: ReactNode }) => <code>{children}</code>,
  MutationDialog: (props: Record<string, unknown>) => {
    mocks.mutationProps = props;
    return null;
  },
  RowsListView: (props: Record<string, unknown>) => {
    mocks.listProps.push(props);
    return <>{props.toolbarActions as ReactNode}</>;
  },
  // The shared picker stands in as its trigger, which takes the form's focus through `controlRef`
  // and, as the real picker's trigger is, is disabled while read-only (a dialog's fields lock as it submits).
  SubjectPicker: ({ controlRef, readOnly, "aria-invalid": invalid }: {
    controlRef?: Ref<HTMLButtonElement>; readOnly?: boolean; "aria-invalid"?: boolean;
  }) => <button type="button" ref={controlRef} disabled={readOnly} aria-label="Member picker" aria-invalid={invalid}>Member picker</button>,
  useAuthoredResourceMutation: () => [mocks.add],
}));

import { invalidFormSubmit, type DescriptorField } from "@angee/ui";
import { createUiTestProviders, recordFocusCalls } from "@angee/ui/testing";

import { GroupMembersTab } from "./GroupAccessTabs";

function recordPanelContext(recordId: string): RecordPanelContext {
  return {
    recordId,
    reload: vi.fn(),
    form: {} as RecordPanelContext["form"],
    focusField: vi.fn(),
    fieldLabel: () => undefined,
  };
}

describe("group access tabs", () => {
  beforeEach(() => {
    mocks.add.mockReset();
    mocks.listProps = [];
    mocks.mutationProps = null;
    mocks.queryData = {
      groups_by_pk: {
        id: "igr_1",
        members: [{
          id: "member-1",
          subject: "auth/user:9",
          subject_type: "auth/user",
          subject_id: "9",
          label: "Service robot",
          caveat_name: "office-hours",
        }],
        bindings: [],
      },
    };
  });

  test("removes the exact canonical member tuple", () => {
    render(<GroupMembersTab {...recordPanelContext("igr_1")} />);
    const [remove] = mocks.listProps[0]?.rowActions as Array<{
      variables: (row: Record<string, string>) => unknown;
    }>;
    if (!remove) throw new Error("expected the remove-member action");
    expect(remove.variables((mocks.queryData as {
      groups_by_pk: { members: Record<string, string>[] };
    }).groups_by_pk.members[0]!)).toEqual({
      group_id: "igr_1",
      subject: "auth/user:9",
      caveat_name: "office-hours",
    });
  });

  test("adds the selected canonical subject with an explicit empty caveat, through the dialog's own call", async () => {
    mocks.add.mockResolvedValue({ add_group_member: true });
    render(<GroupMembersTab {...recordPanelContext("igr_1")} />);
    const mutation = mocks.mutationProps?.mutation as (values: { subject: string }, options: { errorNotification: false }) => Promise<unknown>;
    await expect(mutation({ subject: "auth/user:9" }, { errorNotification: false })).resolves.toEqual({ add_group_member: true });
    // The dialog runs the call and reports a refusal itself (a missing result as this fallback); no toast repeats it.
    expect(mocks.add).toHaveBeenCalledWith({
      group_id: "igr_1",
      subject: "auth/user:9",
      caveat_name: "",
    }, { errorNotification: false });
    expect(mocks.mutationProps?.errorFallback).toBe("Could not add member.");
  });

  test("a refused member brings focus to the member field's picker, not the banner", async () => {
    render(<GroupMembersTab {...recordPanelContext("igr_1")} />);
    const fields = mocks.mutationProps?.fields as readonly DescriptorField[];
    cleanup();
    const ui = await vi.importActual<typeof import("@angee/ui")>("@angee/ui");
    const { Provider, clearClients } = createUiTestProviders({ router: true });
    const refused = async () => invalidFormSubmit({ fieldErrors: { subject: ["Already a member."] }, formErrors: [] });
    render(<Provider><ui.ModalsHost><ui.ToastProvider><ui.AppRuntimeProvider runtime={{ widgets: ui.defaultWidgets }}>
      <ui.MutationDialog open onOpenChange={() => undefined} title="Add member" submitLabel="Add" parseValues={(values) => values}
        fields={fields} initialValues={{ subject: "auth/user:9" }} onSubmit={refused} />
    </ui.AppRuntimeProvider></ui.ToastProvider></ui.ModalsHost></Provider>);
    const add = await screen.findByRole("button", { name: "Add" }) as HTMLButtonElement;
    await waitFor(() => expect(add.disabled).toBe(false));
    add.focus();
    const recorder = recordFocusCalls();
    try {
      fireEvent.click(add);
      await screen.findByText("Already a member.");
      await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Member picker" })));
      // The member field marks its picker invalid.
      expect(document.activeElement?.getAttribute("aria-invalid")).toBe("true");
      // Every focus call reached the picker once it took focus again, and none the banner.
      expect(recorder.calls.length).toBeGreaterThan(0);
      expect(recorder.calls.map((call) => [call.target.getAttribute("aria-label"), call.takesFocus]))
        .toEqual(recorder.calls.map(() => ["Member picker", true]));
    } finally {
      recorder.restore();
      cleanup();
      clearClients();
    }
  });
});
