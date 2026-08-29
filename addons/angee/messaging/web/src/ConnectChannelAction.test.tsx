// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { TypedDocumentNode } from "@angee/refine";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const actionMocks = vi.hoisted(() => ({
  authoredConnect: vi.fn(async (values: unknown) => values),
  authoredOptions: null as Record<string, unknown> | null,
  dialogProps: null as Record<string, unknown> | null,
  pairingConnect: vi.fn(async (values: unknown, nextStep?: () => React.ReactNode) => {
    nextStep?.();
    return values;
  }),
}));

vi.mock("@angee/refine", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...original,
    useAuthoredMutation: (_document: unknown, options?: Record<string, unknown>) => {
      actionMocks.authoredOptions = options ?? null;
      return [actionMocks.authoredConnect, { fetching: false, error: null }];
    },
  };
});

vi.mock("@angee/ui", async (importOriginal) => {
  const original = await importOriginal<typeof import("@angee/ui")>();
  const { createMutationDialogTestDouble } = await import("@angee/ui/testing");
  return {
    ...original,
    Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
      <button {...props}>{children}</button>
    ),
    Glyph: ({ name }: { name: string }) => <span aria-hidden>{name}</span>,
    MutationDialog: createMutationDialogTestDouble({
      capture: (props) => {
        actionMocks.dialogProps = props;
      },
      values: { name: "  Ada  " },
      submitLabel: "submit",
    }),
  };
});

vi.mock("./usePairingConnect", () => ({
  usePairingConnect: () => ({
    connect: actionMocks.pairingConnect,
    connectState: { fetching: false },
    pairingDialog: <div>pairing dialog</div>,
  }),
}));

vi.mock("./i18n", () => ({
  useMessagingT: () => (key: string) => {
    if (key === "channel.connect.submit") return "Connect";
    if (key === "channel.connect.submitting") return "Connecting…";
    if (key === "channel.demo.error") return "Could not connect demo.";
    return key;
  },
}));

import { mutationDialogValueCodecs } from "@angee/ui";
import { ConnectChannelAction } from "./ConnectChannelAction";

type Variables = { name: string };
type Result = { connect_demo_channel: { id: string } };
const document = {} as TypedDocumentNode<Result, Variables>;
const fields = (t: (key: string) => string) => [
  { name: "name", label: t("channel.connect.name"), required: true },
];
const parseValues = (values: Readonly<Record<string, unknown>>): Variables => ({
  name: mutationDialogValueCodecs.requiredString(values.name, "name"),
});

describe("ConnectChannelAction", () => {
  afterEach(cleanup);

  beforeEach(() => {
    actionMocks.authoredConnect.mockClear();
    actionMocks.pairingConnect.mockClear();
    actionMocks.authoredOptions = null;
    actionMocks.dialogProps = null;
  });

  test("owns mutation invalidation, generic busy copy, and dialog defaults", async () => {
    render(
      <ConnectChannelAction
        kind="mutation"
        document={document}
        fields={fields}
        i18nPrefix="channel.demo"
        parseValues={parseValues}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "channel.demo.button" }));

    expect(actionMocks.authoredOptions).toEqual({
      invalidateModels: ["messaging.Channel"],
    });
    expect(actionMocks.dialogProps).toMatchObject({
      title: "channel.demo.title",
      description: "channel.demo.description",
      submitLabel: "Connect",
      submittingLabel: "Connecting…",
      errorFallback: "Could not connect demo.",
    });
    expect(actionMocks.dialogProps).not.toHaveProperty("cancelLabel");

    fireEvent.submit(screen.getByRole("form", { name: "channel.demo.title" }));
    await waitFor(() =>
      expect(actionMocks.authoredConnect).toHaveBeenCalledWith({ name: "Ada" }),
    );
  });

  test("hands successful live-channel creation to the pairing owner", async () => {
    const nextStep = vi.fn(() => <p>next</p>);
    render(
      <ConnectChannelAction
        kind="pairing"
        document={document}
        fields={fields}
        i18nPrefix="channel.demo"
        parseValues={parseValues}
        resultField="connect_demo_channel"
        instructionKey="channel.demo.scan"
        nextStep={nextStep}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "channel.demo.button" }));
    fireEvent.submit(screen.getByRole("form", { name: "channel.demo.title" }));

    await waitFor(() =>
      expect(actionMocks.pairingConnect).toHaveBeenCalledWith(
        { name: "Ada" },
        expect.any(Function),
      ),
    );
    expect(nextStep).toHaveBeenCalledWith({ name: "Ada" }, expect.any(Function));
    expect(screen.getByText("pairing dialog")).toBeTruthy();
  });
});
