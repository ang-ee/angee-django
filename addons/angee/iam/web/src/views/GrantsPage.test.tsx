// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listProps: null as Record<string, unknown> | null,
}));

vi.mock("@angee/ui", () => ({
  Code: ({ children }: { children?: ReactNode }) => <code>{children}</code>,
  ListView: (props: Record<string, unknown>) => {
    mocks.listProps = props;
    return null;
  },
  defineRowAction: (declaration: Record<string, unknown>) => declaration,
}));

vi.mock("../i18n", () => ({
  useIamT: () => (
    key: string,
    values?: Record<string, string>,
  ) => values ? `${key}:${JSON.stringify(values)}` : key,
}));

import { IamRevokeRole } from "../documents";
import { GrantsPage } from "./GrantsPage";

describe("IAM grants page", () => {
  beforeEach(() => {
    mocks.listProps = null;
  });

  test("projects each grant into the shared revoke row action and preserves false-as-failure", () => {
    render(<GrantsPage />);

    expect(mocks.listProps).toMatchObject({
      resource: "iam.Grant",
      defaultGroup: { field: "namespace" },
      pageSize: 50,
    });
    const columns = mocks.listProps?.columns as Array<{
      field: string;
      header?: string;
      headerVisuallyHidden?: boolean;
    }>;
    expect(columns.map((column) => column.field)).toEqual([
      "subject_label",
      "role",
      "namespace",
    ]);
    expect(columns.some((column) => column.header === "")).toBe(false);

    const [revoke] = mocks.listProps?.rowActions as Array<{
      kind: string;
      document: unknown;
      variables: (row: Record<string, string>) => unknown;
      succeeded: (result: { revoke_role: boolean } | undefined) => boolean;
      invalidateModels: readonly string[];
      pendingPolicy: string;
      confirm: { body: (row: Record<string, string>) => ReactNode };
    }>;
    const row = {
      id: "grant-a",
      subject_id: "1",
      subject_type: "auth/user",
      subject: "auth/user:1",
      subject_relation: "",
      subject_label: "Alice",
      role: "angee/role:writer",
      role_name: "Writer",
      namespace: "angee",
      caveat_name: "business-hours",
    };
    expect(revoke?.document).toBe(IamRevokeRole);
    expect(revoke).toMatchObject({ kind: "authored" });
    expect(revoke?.variables(row)).toEqual({
      subject: "auth/user:1",
      role: "angee/role:writer",
      caveat_name: "business-hours",
    });
    expect(revoke?.succeeded({ revoke_role: true })).toBe(true);
    expect(revoke?.succeeded({ revoke_role: false })).toBe(false);
    expect(revoke?.succeeded(undefined)).toBe(false);
    expect(revoke?.invalidateModels).toEqual(["iam.Grant", "iam.Relationship", "iam.Group", "iam.Role"]);
    expect(revoke?.pendingPolicy).toBe("active-row");
    expect(revoke?.confirm.body(row)).toContain("Alice");
  });
});
