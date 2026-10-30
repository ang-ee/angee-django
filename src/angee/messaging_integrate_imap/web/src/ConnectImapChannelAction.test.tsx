// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
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

import { ConnectImapChannelAction } from "./ConnectImapChannelAction";

describe("ConnectImapChannelAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.props = null;
  });

  test("declares IMAP fields and typed variables through the shared factory", () => {
    render(<ConnectImapChannelAction />);
    const fields = actionMocks.props?.fields as (
      t: (key: string) => string,
    ) => readonly { name: string }[];
    const parseValues = actionMocks.props?.parseValues as (
      values: Record<string, unknown>,
      t: (key: string, vars?: Record<string, unknown>) => string,
    ) => unknown;

    expect(actionMocks.props).toMatchObject({
      kind: "mutation",
      i18nPrefix: "channel.imap",
      initialValues: { security: "ssl" },
    });
    expect(fields((key) => key).map((field) => field.name)).toEqual([
      "name",
      "host",
      "security",
      "port",
      "username",
      "password",
      "mailboxes",
      "ownAddresses",
    ]);
    expect(
      parseValues({
        name: " Ada Mail ",
        host: " imap.example.com ",
        security: "starttls",
        port: "143",
        username: " ada@example.com ",
        password: " mail-password ",
        mailboxes: "INBOX\nArchive",
        ownAddresses: "ada@example.com\nalias@example.com",
      }, (key, vars) => vars?.label ? `${String(vars.label)} must be a whole number.` : key),
    ).toEqual({
      name: "Ada Mail",
      host: "imap.example.com",
      security: "starttls",
      port: 143,
      username: "ada@example.com",
      password: " mail-password ",
      mailboxes: ["INBOX", "Archive"],
      ownAddresses: ["ada@example.com", "alias@example.com"],
    });
    expect(() =>
      parseValues(
        {
          name: "Ada Mail",
          host: "imap.example.com",
          security: "ssl",
          port: "143.5",
          username: "ada@example.com",
          password: "secret",
        },
        (key, vars) =>
          vars?.label ? `${String(vars.label)} must be a whole number.` : key,
      ),
    ).toThrow("channel.imap.port must be a whole number.");
  });
});
