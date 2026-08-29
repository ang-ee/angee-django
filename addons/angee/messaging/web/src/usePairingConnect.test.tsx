// @vitest-environment happy-dom

import type { TypedDocumentNode } from "@angee/refine";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const hookMocks = vi.hoisted(() => ({
  mutate: vi.fn(async () => ({ connect_example_channel: { id: "chn_1" } })),
  mutationOptions: null as Record<string, unknown> | null,
  pairingProps: null as Record<string, unknown> | null,
}));

vi.mock("@angee/refine", () => ({
  useAuthoredMutation: (_document: unknown, options: Record<string, unknown>) => {
    hookMocks.mutationOptions = options;
    return [hookMocks.mutate, { fetching: false, error: null }];
  },
}));

vi.mock("./documents", () => ({
  CHANNEL_MODEL: "messaging.Channel",
}));

vi.mock("./PairingDialog", () => ({
  PairingDialog: (props: Record<string, unknown>) => {
    hookMocks.pairingProps = props;
    return props.channelId ? (
      <div role="dialog">
        {props.instruction as string}
        {props.nextStep as React.ReactNode}
      </div>
    ) : null;
  },
}));

import { usePairingConnect } from "./usePairingConnect";

type ConnectData = {
  connect_example_channel: { id: string } | null;
};
type ConnectVariables = { name: string };
const ConnectExampleChannel = {} as TypedDocumentNode<
  ConnectData,
  ConnectVariables
>;

function Harness(): React.ReactElement {
  const { connect, connectState, pairingDialog } = usePairingConnect(
    ConnectExampleChannel,
    "connect_example_channel",
    "Scan in Example.",
  );
  return (
    <>
      <button
        type="button"
        disabled={connectState.fetching}
        onClick={() =>
          void connect({ name: "Example" }, () => (
            <a href="https://example.test/next">Continue</a>
          ))
        }
      >
        Connect
      </button>
      {pairingDialog}
    </>
  );
}

describe("usePairingConnect", () => {
  afterEach(cleanup);

  beforeEach(() => {
    hookMocks.mutate.mockClear();
    hookMocks.mutationOptions = null;
    hookMocks.pairingProps = null;
  });

  test("invalidates channels and opens then closes pairing for the returned id", async () => {
    render(<Harness />);

    expect(hookMocks.mutationOptions).toEqual({
      invalidateModels: ["messaging.Channel"],
    });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(hookMocks.mutate).toHaveBeenCalledWith({ name: "Example" }),
    );
    await waitFor(() => expect(screen.getByRole("dialog")).toBeTruthy());
    expect(hookMocks.pairingProps).toMatchObject({
      channelId: "chn_1",
      instruction: "Scan in Example.",
    });
    expect(screen.getByRole("link", { name: "Continue" })).toBeTruthy();

    act(() => {
      (hookMocks.pairingProps?.onClose as () => void)();
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByRole("link", { name: "Continue" })).toBeNull();
  });
});
