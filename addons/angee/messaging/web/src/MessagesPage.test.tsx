// @vitest-environment happy-dom

import { render } from "@testing-library/react";
import * as React from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

const pageMocks = vi.hoisted(() => ({
  resourceProps: null as Record<string, unknown> | null,
  listProps: null as Record<string, unknown> | null,
  columnFields: [] as string[],
}));

vi.mock("@angee/ui", () => ({
  Action: () => null,
  Column: ({ field }: { field: string }) => {
    pageMocks.columnFields.push(field);
    return null;
  },
  Facet: () => null,
  Field: () => null,
  Form: ({ children }: { children?: React.ReactNode }) => <section>{children}</section>,
  Group: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  List: (props: Record<string, unknown>) => {
    pageMocks.listProps = props;
    return <section>{props.children as React.ReactNode}</section>;
  },
  ResourceList: (props: Record<string, unknown>) => {
    pageMocks.resourceProps = props;
    return <div>{props.children as React.ReactNode}</div>;
  },
}));

vi.mock("./i18n", () => ({
  useMessagingT: () => (key: string) => key,
}));

import { MessagesPage } from "./MessagesPage";

describe("MessagesPage", () => {
  beforeEach(() => {
    pageMocks.resourceProps = null;
    pageMocks.listProps = null;
    pageMocks.columnFields = [];
  });

  test("uses readable relation axes for inbox grouping and sender display", () => {
    render(<MessagesPage />);

    expect(pageMocks.resourceProps).toMatchObject({
      resource: "messaging.Message",
      placement: "inline",
      routed: true,
      hideCreate: true,
    });
    expect(pageMocks.listProps).toMatchObject({
      resource: "messaging.Message",
      defaultGroups: { list: { field: "channel.display_name" } },
    });
    expect(pageMocks.columnFields).toEqual(
      expect.arrayContaining(["title", "sender", "thread.title.text", "status", "sent_at"]),
    );
    expect(pageMocks.columnFields).not.toContain("sender.value");
  });
});
