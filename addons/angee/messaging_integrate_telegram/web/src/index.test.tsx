import { expectValidBaseAddon } from "@angee/app/testing";
import {
  INTEGRATION_DISCONNECT_ACTION_ID,
  INTEGRATION_RESUME_ACTION_ID,
} from "@angee/integrate";
import { CHANNEL_MODEL, MESSAGING_CHANNEL_TOOLBAR_SLOT } from "@angee/messaging";
import {
  formViewRecordActionsSlot,
  type RecordChromeContext,
} from "@angee/ui";
import type * as React from "react";
import { describe, expect, test } from "vitest";

import {
  TELEGRAM_BACKEND,
  TELEGRAM_CONNECT_ACTION_ID,
  TELEGRAM_PAIRING_ACTION_ID,
  default as messagingIntegrateTelegram,
} from "./index";

describe("messaging_integrate_telegram addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(messagingIntegrateTelegram)).not.toThrow();
  });

  test("registers the Telegram connect action and messaging bundle", () => {
    expect(messagingIntegrateTelegram.slots?.[0]).toMatchObject({
      slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
      id: TELEGRAM_CONNECT_ACTION_ID,
      sequence: 21,
    });
    expect(messagingIntegrateTelegram.i18n?.messaging?.["channel.telegram.scan"]).toBe(
      "Open Telegram on your phone → Settings → Devices → Link Desktop Device, then scan this code.",
    );
    expect(messagingIntegrateTelegram.i18n?.messaging?.["channel.telegram.keysHelp"]).toBe(
      "Create or copy your Telegram application keys.",
    );
  });

  test("pins generic pairing lifecycle predicates and resume-on-open semantics", () => {
    const slot = formViewRecordActionsSlot(CHANNEL_MODEL, TELEGRAM_BACKEND);
    const actions = (messagingIntegrateTelegram.slots ?? [])
      .filter((entry) => entry.slot === slot)
      .map((entry) => ({
        id: entry.id,
        props: (entry.content as React.ReactElement<{
          instructionKey?: string;
          labelKey?: string;
          resumeOnOpen?: boolean;
          when?: (context: RecordChromeContext) => boolean;
        }>).props,
      }));

    expect(actions.map(({ id }) => id)).toEqual([
      TELEGRAM_CONNECT_ACTION_ID,
      TELEGRAM_PAIRING_ACTION_ID,
      INTEGRATION_RESUME_ACTION_ID,
      INTEGRATION_DISCONNECT_ACTION_ID,
    ]);
    expect(actions.slice(0, 3).map(({ props }) => props.instructionKey)).toEqual([
      "channel.telegram.scan",
      "channel.telegram.scan",
      "channel.telegram.scan",
    ]);
    expect(actions.slice(0, 3).map(({ props }) => props.resumeOnOpen)).toEqual([
      true,
      undefined,
      true,
    ]);
    for (const [lifecycle, expected] of [
      ["DISCONNECTED", [true, false, false]],
      ["CONNECTED", [false, true, false]],
      ["PAUSED", [false, false, true]],
    ] as const) {
      const context: RecordChromeContext = {
        resource: CHANNEL_MODEL,
        dataProviderName: "console",
        canonicalResource: "integrate.Integration",
        recordId: "chn_1",
        record: { lifecycle },
      };
      expect(actions.slice(0, 3).map(({ props }) => props.when?.(context))).toEqual(expected);
    }
  });

  test("contributes every channel-prefixed verb at Telegram's impl key only", () => {
    // The model belongs to messaging, not this vendor addon. A bare model-key
    // contribution would displace integrate's verb for WhatsApp and IMAP too.
    const recordActions = (messagingIntegrateTelegram.slots ?? []).filter((entry) =>
      entry.slot.startsWith(formViewRecordActionsSlot(CHANNEL_MODEL)),
    );
    expect(recordActions.map((entry) => entry.slot)).toEqual(
      recordActions.map(() =>
        formViewRecordActionsSlot(CHANNEL_MODEL, TELEGRAM_BACKEND),
      ),
    );
    expect(
      recordActions.filter(
        (entry) => entry.slot === formViewRecordActionsSlot(CHANNEL_MODEL),
      ),
    ).toEqual([]);
  });

  test("contributes a Telegram menu under Messaging", () => {
    expect(messagingIntegrateTelegram.menus?.[0]).toMatchObject({
      id: "messaging.telegram",
      label: "Telegram",
      to: "/messaging/channels",
      parentId: "messaging",
      description: "Link Telegram accounts by QR code",
    });
  });
});
