// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  declared: [] as Record<string, unknown>[],
}));

vi.mock("@angee/integrate", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/integrate")>()),
  ConditionalMutationButton: (props: Record<string, unknown>) => {
    mocks.declared.push(props);
    return (
      <button type="button" data-field={String(props.field)}>
        {props.label as React.ReactNode}
      </button>
    );
  },
}));

vi.mock("./i18n", () => ({
  useMessagingTelegramT: () => (key: string) => key,
}));

import { TelegramDisconnectAction } from "./TelegramDisconnectAction";

describe("TelegramDisconnectAction", () => {
  afterEach(() => {
    cleanup();
    mocks.declared.length = 0;
  });

  test("specializes the retained-session disconnect verb with Telegram copy", () => {
    render(<TelegramDisconnectAction />);

    expect(screen.getByRole("button", { name: "channel.telegram.disconnect" }).dataset.field)
      .toBe("disconnect_channel");
    expect(mocks.declared[0]).toMatchObject({
      variant: "danger",
      confirm: {
        title: "channel.telegram.disconnectConfirm.title",
        body: "channel.telegram.disconnectConfirm.body",
        danger: true,
      },
    });
  });
});
