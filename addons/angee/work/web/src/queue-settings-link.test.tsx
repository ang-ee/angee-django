// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  recordHref: undefined as ((id: string) => string | undefined) | undefined,
  resource: "",
}));

vi.mock("@angee/ui", () => ({
  Button: ({ children }: { children?: ReactNode }) => <>{children}</>,
  NavLink: ({ href, children }: { href?: string; children?: ReactNode }) => <a href={href}>{children}</a>,
  useResourceRecordHref: (resource: string) => {
    mocks.resource = resource;
    return mocks.recordHref;
  },
}));

vi.mock("./i18n", () => ({ useWorkT: () => (key: string) => key }));

import { QueueSettingsLink } from "./queue-settings-link";

afterEach(() => {
  cleanup();
  mocks.recordHref = undefined;
});

describe("QueueSettingsLink", () => {
  test("opens the queue's record wherever composition routes it", () => {
    mocks.recordHref = (id) => `/work/queues/${id}`;
    const { getByRole } = render(<QueueSettingsLink queueId="que_eng" />);
    expect(mocks.resource).toBe("work.Queue");
    expect(getByRole("link", { name: "queue.settings" }).getAttribute("href")).toBe("/work/queues/que_eng");
  });

  test("renders nothing when the queue record is not routed or no queue is open", () => {
    expect(render(<QueueSettingsLink queueId="que_eng" />).container.firstChild).toBeNull();
    mocks.recordHref = (id) => `/work/queues/${id}`;
    expect(render(<QueueSettingsLink queueId="" />).container.firstChild).toBeNull();
  });
});
