import { expectValidBaseAddon } from "@angee/app/testing";
import {
  INTEGRATION_DISCONNECT_ACTION_ID,
  INTEGRATION_RESUME_ACTION_ID,
} from "@angee/integrate";
import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "@angee/messaging";
import { formViewRecordActionsSlot } from "@angee/ui";
import type * as React from "react";
import { describe, expect, test } from "vitest";

import { CHANNEL_MODEL, WHATSAPP_BACKEND } from "./channel";
import messagingIntegrateWhatsapp from "./index";
import {
  WHATSAPP_CONNECT_ACTION_ID,
  WHATSAPP_PAIRING_ACTION_ID,
} from "./WhatsappConnectionAction";

describe("messaging_integrate_whatsapp addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messagingIntegrateWhatsapp)).not.toThrow();
  });

  test("contributes the WhatsApp connect action to messaging's channel toolbar", () => {
    expect(messagingIntegrateWhatsapp.slots?.[0]).toMatchObject({
      slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
      id: "messaging-integrate-whatsapp.connect",
      sequence: 20,
    });
    expect(messagingIntegrateWhatsapp.i18n?.messaging?.["channel.whatsapp.button"]).toBe(
      "Connect WhatsApp",
    );
  });

  test("contributes its record verbs at its own backend's impl key", () => {
    // The impl key is what scopes these to a WhatsApp-backed row. `messaging`
    // owns `messaging.Channel`, so contributing at the bare model key would
    // displace integrate's verbs for *every* channel — IMAP's included — and cap
    // the model at one vendor (a second backend claiming the same id there throws
    // at composition).
    const recordActions = (messagingIntegrateWhatsapp.slots ?? []).filter((entry) =>
      entry.slot.startsWith(formViewRecordActionsSlot(CHANNEL_MODEL)),
    );

    expect(recordActions.map((entry) => entry.slot)).toEqual(
      recordActions.map(() =>
        formViewRecordActionsSlot(CHANNEL_MODEL, WHATSAPP_BACKEND),
      ),
    );
    expect(recordActions.map((entry) => entry.id)).toEqual([
      // Connecting a channel is a QR pairing, not a flag flip, so it has no
      // shared default to specialize and carries this addon's own id. Reading a
      // connected channel's pairing moves no lifecycle at all, so it carries one
      // too.
      WHATSAPP_CONNECT_ACTION_ID,
      WHATSAPP_PAIRING_ACTION_ID,
      // These two carry integrate's ids, so they replace the inherited verbs
      // for a WhatsApp channel only.
      INTEGRATION_RESUME_ACTION_ID,
      INTEGRATION_DISCONNECT_ACTION_ID,
    ]);
  });

  test("reaches a connected channel, the only state its repair is offered in", () => {
    // The gap this guards: `_pairing_state` reports LOGGED_OUT/DUPLICATE_ACCOUNT
    // only while the lifecycle is CONNECTED (the worker reports runtime state; it
    // does not overwrite the operator's intent), and the dialog offering the
    // `resetWhatsappPairing` repair for those two states opens from here. Gate every
    // entry on disconnected/paused and the documented repair has no console path.
    const lifecycles = (messagingIntegrateWhatsapp.slots ?? [])
      .filter((entry) => entry.slot === formViewRecordActionsSlot(CHANNEL_MODEL, WHATSAPP_BACKEND))
      .map((entry) => {
        const content = entry.content as React.ReactElement<{ lifecycle?: string }>;
        return content.props.lifecycle;
      });

    expect(lifecycles).toContain("connected");
  });

  test("contributes nothing at the bare channel-model key", () => {
    // The regression this guards: an entry keyed on `messaging.Channel` alone
    // reaches every channel, whatever its backend.
    const bare = formViewRecordActionsSlot(CHANNEL_MODEL);
    expect(
      (messagingIntegrateWhatsapp.slots ?? []).filter((entry) => entry.slot === bare),
    ).toEqual([]);
  });

  test("contributes a WhatsApp-labelled menu entry under Messaging", () => {
    expect(messagingIntegrateWhatsapp.menus?.[0]).toMatchObject({
      id: "messaging.whatsapp",
      label: "WhatsApp",
      to: "/messaging/channels",
      parentId: "messaging",
      description: "Link WhatsApp accounts by QR code",
    });
  });
});
