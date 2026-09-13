// @vitest-environment happy-dom

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  model: { resource: { subjectField: "assignmentSubject", roots: { list: "users" } } } as unknown,
  relationProps: null as Record<string, unknown> | null,
  enabled: false,
}));

vi.mock("@angee/metadata", () => ({
  rowPublicId: (row: { id: string }) => row.id,
  rowValueAtPath: (row: Record<string, unknown>, path: string) => row[path],
  useModelMetadata: () => mocks.model,
}));

vi.mock("../resource/model-metadata-defaults", () => ({
  relationFieldInfoForResource: () => ({ resource: "iam.User", labelField: "label" }),
}));

vi.mock("../relation/relation-options", () => ({
  useRelationOptions: (_info: unknown, config: { enabled: boolean }) => {
    mocks.enabled = config.enabled;
    return {
      list: { fetching: false, error: undefined, refetch: vi.fn() },
      options: [{ value: "usr_1", label: "Alice" }],
      rows: [{ id: "usr_1", assignmentSubject: "auth/user:1" }],
    };
  },
}));

vi.mock("../relation/RelationPicker", () => ({
  RelationPicker: (props: Record<string, unknown>) => {
    mocks.relationProps = props;
    return <button type="button">picker</button>;
  },
}));

import { SubjectPicker } from "./SubjectPicker";

describe("SubjectPicker", () => {
  beforeEach(() => {
    mocks.model = { resource: { subjectField: "assignmentSubject", roots: { list: "users" } } };
    mocks.relationProps = null;
    mocks.enabled = false;
  });
  afterEach(() => cleanup());

  test("reports a resource without a list root instead of mounting a picker", () => {
    mocks.model = { resource: { subjectField: "assignmentSubject", roots: {} } };
    render(<SubjectPicker resource="iam.User" value="" onChange={vi.fn()} />);
    expect(screen.getByText("This recipient type has no selectable resource.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "picker" })).toBeNull();
  });

  test("maps public option ids to canonical subjects and clears a controlled selection", () => {
    const onChange = vi.fn();
    const view = render(<SubjectPicker resource="iam.User" value="" onChange={onChange} />);
    act(() => (mocks.relationProps?.onChange as (id: string) => void)("usr_1"));
    expect(onChange).toHaveBeenCalledWith("auth/user:1");

    view.rerender(<SubjectPicker resource="iam.User" value="" onChange={onChange} />);
    expect(mocks.relationProps?.value).toBe("");
  });

  test("restores a controlled canonical subject from its loaded option row", () => {
    render(<SubjectPicker resource="iam.User" value="auth/user:1" onChange={vi.fn()} />);
    expect(mocks.relationProps?.value).toBe("usr_1");
  });

  test("enables option loading only while the picker is open", () => {
    const view = render(<SubjectPicker resource="iam.User" value="" onChange={vi.fn()} />);
    act(() => (mocks.relationProps?.onOpenChange as (open: boolean) => void)(true));
    view.rerender(<SubjectPicker resource="iam.User" value="" onChange={vi.fn()} />);
    expect(mocks.enabled).toBe(true);
    act(() => (mocks.relationProps?.onOpenChange as (open: boolean) => void)(false));
    view.rerender(<SubjectPicker resource="iam.User" value="" onChange={vi.fn()} />);
    expect(mocks.enabled).toBe(false);
  });
});
