// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  pageProps: null as Record<string, unknown> | null,
  declarationProps: null as Record<string, unknown> | null,
}));

vi.mock("@angee/ui", () => ({
  Column: () => null,
  List: () => null,
  ResourceList: (props: Record<string, unknown> & { children?: React.ReactNode }) => {
    mocks.pageProps = props;
    return null;
  },
  useRouteHref: () => () => "/work/queues/que_eng/board",
  useRouteRecordId: () => "que_eng",
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("../i18n", () => ({
  useWorkT: () => (key: string) => key,
}));

vi.mock("../queue-form", () => ({
  useQueueFormDeclaration: (props?: Record<string, unknown>) => {
    mocks.declarationProps = props ?? {};
    return <div data-queue-form />;
  },
}));

import { QueuesPage } from "./QueuesPage";

beforeEach(() => {
  mocks.pageProps = null;
  mocks.declarationProps = null;
});

afterEach(cleanup);

describe("queue page", () => {
  test("its routed record is the shared queue settings form: one stacked sheet, no record tabs", () => {
    render(<QueuesPage />);

    expect(mocks.declarationProps).toEqual({});
    expect(mocks.pageProps?.recordTabs).toBeUndefined();
    const children = React.Children.toArray(mocks.pageProps?.children as React.ReactNode) as React.ReactElement[];
    expect(children.some((child) => (child.props as Record<string, unknown>)["data-queue-form"] !== undefined)).toBe(true);
    expect((mocks.pageProps?.recordSmartButtons as readonly { id: string }[]).map((button) => button.id))
      .toEqual(["work-queue-board", "work-queue-triage", "work-queue-cycles"]);
  });
});
