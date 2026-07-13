// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  listViews: [] as Record<string, unknown>[],
  columnFields: [] as string[],
  recordHrefResources: [] as string[],
}));

vi.mock("@angee/ui", () => ({
  Action: () => null,
  Column: ({ field }: { field: string }) => {
    pageMocks.columnFields.push(field);
    return null;
  },
  Field: () => null,
  Form: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
  Group: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  List: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
  ListView: (props: Record<string, unknown>) => {
    pageMocks.listViews.push(props);
    return null;
  },
  ResourceList: (props: Record<string, unknown>) => {
    pageMocks.resourceProps = props;
    return <div>{props.children as React.ReactNode}</div>;
  },
  useResourceRecordHref: (resource: string) => {
    pageMocks.recordHrefResources.push(resource);
    return (id: string) => `/routed/${encodeURIComponent(id)}`;
  },
}));

vi.mock("./i18n", () => ({
  useSpacesT: () => (key: string) => key,
}));

import { SpacesPage } from "./SpacesPage";

describe("SpacesPage", () => {
  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.listViews = [];
    pageMocks.columnFields = [];
    pageMocks.recordHrefResources = [];
  });

  test("composes the group resource and scoped roster/thread primitives", () => {
    render(<SpacesPage />);

    expect(pageMocks.resourceProps).toMatchObject({
      resource: "spaces.Group",
      placement: "inline",
      routed: true,
    });
    expect(pageMocks.columnFields).toEqual(
      expect.arrayContaining(["name", "parent.name", "visibility", "created_at"]),
    );

    const tabs = pageMocks.resourceProps?.recordTabs as Array<{
      id: string;
      render: (context: { recordId: string }) => React.ReactNode;
    }>;
    for (const tab of tabs) {
      render(<>{tab.render({ recordId: "grp_1" })}</>);
    }

    expect(pageMocks.listViews[0]).toMatchObject({
      resource: "spaces.Membership",
      scope: "local",
      baseFilter: { group: { exact: "grp_1" } },
    });
    expect(pageMocks.listViews[1]).toMatchObject({
      resource: "spaces.GroupThread",
      scope: "local",
      baseFilter: { group: { exact: "grp_1" } },
    });
    expect(pageMocks.recordHrefResources).toEqual(["messaging.Thread"]);
    const threadHref = pageMocks.listViews[1]?.rowHref as (row: { id: string }) => string;
    expect(threadHref({ id: "thr 1" })).toBe("/routed/thr%201");
  });
});
