// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
}));

vi.mock("@angee/messaging", () => ({
  ConnectChannelAction: (props: Record<string, unknown>) => {
    actionMocks.props = props;
    return <button type="button">connect</button>;
  },
}));

import { ConnectSlackChannelAction } from "./ConnectSlackChannelAction";

describe("ConnectSlackChannelAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.props = null;
  });

  test("declares Slack fields and typed variables through the shared factory", () => {
    render(<ConnectSlackChannelAction />);
    const fields = actionMocks.props?.fields as (
      t: (key: string) => string,
    ) => readonly { name: string; description?: React.ReactNode }[];
    const declaredFields = fields((key) => key);
    const parseValues = actionMocks.props?.parseValues as (
      values: Record<string, unknown>,
    ) => unknown;

    expect(actionMocks.props).toMatchObject({
      kind: "mutation",
      i18nPrefix: "channel.slack",
    });
    expect(declaredFields.map((field) => field.name)).toEqual(["name", "token"]);
    render(<>{declaredFields[1]?.description}</>);
    expect(
      screen.getByRole("link", { name: "channel.slack.appsLink" }).getAttribute("href"),
    ).toBe("https://api.slack.com/apps");
    expect(parseValues({ name: " Acme ", token: " xoxp-user-token " })).toEqual({
      name: "Acme",
      token: "xoxp-user-token",
    });
  });
});
