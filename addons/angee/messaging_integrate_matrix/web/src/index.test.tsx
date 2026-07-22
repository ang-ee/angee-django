import { expectValidBaseAddon } from "@angee/app/testing";
import { CHANNEL_MODEL, MESSAGING_CHANNEL_TOOLBAR_SLOT } from "@angee/messaging";
import { formViewRecordActionsSlot } from "@angee/ui";
import { describe, expect, test } from "vitest";

import { MATRIX_BACKEND, default as messagingIntegrateMatrix } from "./index";

describe("messaging_integrate_matrix addon manifest", () => {
  test("declares a valid bridge at the Matrix implementation key", () => {
    expect(() => expectValidBaseAddon(messagingIntegrateMatrix)).not.toThrow();
    expect(messagingIntegrateMatrix.slots?.[0]).toMatchObject({
      slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
      id: "messaging-integrate-matrix.connect",
      sequence: 23,
    });
    const actions = (messagingIntegrateMatrix.slots ?? []).slice(1);
    expect(actions.map((entry) => entry.slot)).toEqual(
      actions.map(() => formViewRecordActionsSlot(CHANNEL_MODEL, MATRIX_BACKEND)),
    );
  });

  test("contributes Matrix navigation and recovery-key copy", () => {
    expect(messagingIntegrateMatrix.menus?.[0]).toMatchObject({
      id: "messaging.matrix",
      label: "Matrix",
      parentId: "messaging",
      description: "Connect your Matrix account",
    });
    expect(messagingIntegrateMatrix.i18n?.messaging?.["channel.matrix.recovery"]).toContain(
      "recovery key",
    );
  });
});
