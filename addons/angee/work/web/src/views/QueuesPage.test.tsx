// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  pageProps: null as Record<string, unknown> | null,
  formProps: null as Record<string, unknown> | null,
}));

const CATEGORY_OPTIONS = ["triage", "backlog", "unstarted", "started", "completed", "canceled", "duplicate"]
  .map((value) => ({ value, label: value }));

vi.mock("@angee/ui", () => ({
  Badge: ({ children }: { children?: React.ReactNode }) => <span data-badge>{children}</span>,
  Column: () => null,
  Field: () => null,
  Form: (props: Record<string, unknown>) => {
    mocks.formProps = props;
    return null;
  },
  Group: () => null,
  List: () => null,
  ResourceList: (props: Record<string, unknown> & { children?: React.ReactNode }) => {
    mocks.pageProps = props;
    return <>{props.children}</>;
  },
  useEnumOptions: (_resource: string, field: string) => (field === "category" ? CATEGORY_OPTIONS : []),
  useRouteHref: () => () => "/work/queues/que_eng/board",
  useRouteRecordId: () => "que_eng",
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("../i18n", () => ({
  useWorkT: () => (key: string) => key,
}));

import { QueuesPage } from "./QueuesPage";

interface LineField {
  name: string;
  label?: string;
  description?: string;
  options?: readonly { value: string; disabled?: boolean }[];
}

interface SupplementalColumn {
  key: string;
  render: (row: Record<string, unknown>, parent: null, index: number, context: { formIsDirty: boolean }) => React.ReactNode;
}

beforeEach(() => {
  mocks.pageProps = null;
  mocks.formProps = null;
});

afterEach(cleanup);

describe("queue settings record", () => {
  test("is one stacked form: titled groups, then the stages as lines, with no record tabs", () => {
    render(<QueuesPage />);

    expect(mocks.pageProps?.recordTabs).toBeUndefined();
    expect(mocks.formProps?.layout).toBeUndefined();
    expect(mocks.formProps?.linesTabLabel).toBe("queue.stages.title");
    const groups = React.Children.toArray(mocks.formProps?.children as React.ReactNode)
      .filter((child): child is React.ReactElement<{ label?: string }> =>
        React.isValidElement(child) && (child.props as { label?: string }).label !== undefined);
    expect(groups.map((group) => group.props.label)).toEqual([
      "queue.group.identity",
      "queue.group.triage",
      "queue.group.cadence",
      "queue.group.estimates",
    ]);
  });

  test("stage lines show name, category and tone; the rule flags wait in the visible-fields menu with their help", () => {
    render(<QueuesPage />);

    expect(mocks.formProps?.linePrimaryFields).toEqual(["name", "category", "tone"]);
    const fields = mocks.formProps?.lineFields as readonly LineField[];
    expect(fields.map((field) => field.name)).toEqual(["name", "category", "tone", "rule_owned", "conceals"]);
    const byName = new Map(fields.map((field) => [field.name, field]));
    expect(byName.get("rule_owned")).toMatchObject({ label: "stage.ruleOwned", description: "stage.ruleOwned.help" });
    expect(byName.get("conceals")).toMatchObject({ label: "stage.conceals", description: "stage.conceals.help" });
  });

  test("a stage picks only a custom category; system categories stay listed but disabled", () => {
    render(<QueuesPage />);

    const category = (mocks.formProps?.lineFields as readonly LineField[]).find((field) => field.name === "category");
    const disabled = category?.options?.filter((option) => option.disabled).map((option) => option.value);
    const enabled = category?.options?.filter((option) => !option.disabled).map((option) => option.value);
    expect(disabled).toEqual(["triage", "duplicate"]);
    expect(enabled).toEqual(["backlog", "unstarted", "started", "completed", "canceled"]);
  });

  test("each stage shows its rule behaviour as badges from the live row", () => {
    render(<QueuesPage />);

    const [badges] = mocks.formProps?.lineSupplementalColumns as readonly SupplementalColumn[];
    cleanup();
    render(<>
      {badges!.render({ rule_owned: true, conceals: false }, null, 0, { formIsDirty: false })}
      {badges!.render({ rule_owned: false, conceals: true }, null, 1, { formIsDirty: false })}
      {badges!.render({ rule_owned: false, conceals: false }, null, 2, { formIsDirty: false })}
    </>);
    expect(screen.getAllByText("stage.ruleOwned")).toHaveLength(1);
    expect(screen.getAllByText("stage.conceals")).toHaveLength(1);
  });
});
