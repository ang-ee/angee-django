import { defineBaseAddon, type BaseAddon } from "@angee/app";
import {
  ConditionalMutationButton,
  INTEGRATION_DISCONNECT_ACTION_ID,
  INTEGRATION_RESUME_ACTION_ID,
  integrationLifecycleIs,
  isConnectedOrPaused,
  type IntegrationLifecycleToken,
} from "@angee/integrate";
import { formViewRecordActionsSlot } from "@angee/ui";
import type { ReactNode } from "react";

import { CHANNEL_MODEL } from "./documents";
import { useMessagingT } from "./i18n";
import { ChannelPairingAction } from "./PairingDialog";
import { MESSAGING_CHANNEL_TOOLBAR_SLOT } from "./slots";

export interface ChannelPollBridgeAddonOptions {
  /** Stable addon id; also owns the connect contribution id. */
  id: string;
  /** Channel backend registry key. */
  key: string;
  /** Toolbar contribution order. */
  sequence: number;
  /** Vendor-owned channel creation action. */
  connectAction: ReactNode;
  /** Messaging-namespace messages, including this vendor's menu copy. */
  i18n: Record<string, string>;
}

export interface ChannelBridgeAddonOptions extends ChannelPollBridgeAddonOptions {
  /** Messaging-namespace QR instruction key. */
  instructionKey?: string;
  /** Optional specialization of the generic retained-material disconnect verb. */
  disconnectAction?: ReactNode;
}

/**
 * Declare one live channel vendor's complete rendered-addon manifest.
 *
 * Messaging owns the channel model, pairing dialog, lifecycle verb layout, and
 * channel navigation. A vendor supplies only its identifiers, create action,
 * copy, and scan instruction; every record verb is scoped to the vendor's impl
 * key so it specializes the shared Integration actions for those rows only.
 */
export function defineChannelBridgeAddon({
  id,
  key,
  sequence,
  connectAction,
  i18n,
  instructionKey,
  disconnectAction = <ChannelDisconnectAction />,
}: ChannelBridgeAddonOptions): BaseAddon {
  const connectActionId = `${id}.connect`;
  const pairingActionId = `${id}.pairing`;
  const channelActions = formViewRecordActionsSlot(CHANNEL_MODEL, key);
  const pairingAction = (
    lifecycle: IntegrationLifecycleToken,
    labelKey: string,
    resumeOnOpen?: boolean,
  ): ReactNode => (
    <ChannelPairingAction
      labelKey={labelKey}
      {...(instructionKey ? { instructionKey } : {})}
      {...(resumeOnOpen ? { resumeOnOpen: true } : {})}
      when={integrationLifecycleIs(lifecycle)}
    />
  );

  return defineBaseAddon({
    id,
    i18n: { messaging: i18n },
    menus: [channelBridgeMenu(i18n, key)],
    slots: [
      channelBridgeConnectSlot(id, sequence, connectAction),
      {
        slot: channelActions,
        id: connectActionId,
        sequence: 10,
        content: pairingAction("disconnected", "channel.pairing.connect", true),
      },
      {
        slot: channelActions,
        id: pairingActionId,
        sequence: 10,
        content: pairingAction("connected", "channel.pairing.status"),
      },
      {
        slot: channelActions,
        id: INTEGRATION_RESUME_ACTION_ID,
        sequence: 12,
        content: pairingAction("paused", "channel.pairing.resume", true),
      },
      {
        slot: channelActions,
        id: INTEGRATION_DISCONNECT_ACTION_ID,
        sequence: 13,
        content: disconnectAction,
      },
    ],
  });
}

/** Declare one poll channel vendor's navigation and connect contribution. */
export function defineChannelPollBridgeAddon({
  id,
  key,
  sequence,
  connectAction,
  i18n,
}: ChannelPollBridgeAddonOptions): BaseAddon {
  return defineBaseAddon({
    id,
    i18n: { messaging: i18n },
    menus: [channelBridgeMenu(i18n, key)],
    slots: [channelBridgeConnectSlot(id, sequence, connectAction)],
  });
}

/** Emit one vendor entry under Messaging. */
function channelBridgeMenu(i18n: Record<string, string>, key: string) {
  return {
    id: `messaging.${key}`,
    label: vendorMenuMessage(i18n, key, "label"),
    to: "/messaging/channels",
    parentId: "messaging",
    icon: "channel",
    description: vendorMenuMessage(i18n, key, "description"),
  };
}

/** Emit one vendor connect action in the shared channel toolbar. */
function channelBridgeConnectSlot(id: string, sequence: number, connectAction: ReactNode) {
  return {
    slot: MESSAGING_CHANNEL_TOOLBAR_SLOT,
    id: `${id}.connect`,
    sequence,
    content: connectAction,
  };
}

/** Default disconnect for live channels whose reusable pairing material remains. */
function ChannelDisconnectAction() {
  const t = useMessagingT();

  return (
    <ConditionalMutationButton
      field="disconnect_channel"
      label={t("channel.pairing.disconnect")}
      variant="danger"
      when={isConnectedOrPaused}
      confirm={{
        title: t("channel.pairing.disconnectConfirm.title"),
        body: t("channel.pairing.disconnectConfirm.body"),
        danger: true,
      }}
    />
  );
}

/** Read required vendor menu copy from the vendor's messaging bundle. */
function vendorMenuMessage(
  i18n: Record<string, string>,
  key: string,
  field: "label" | "description",
): string {
  const messageKey = `channel.${key}.menu.${field}`;
  const message = i18n[messageKey];
  if (!message) {
    throw new Error(`Channel bridge ${key} is missing i18n message ${messageKey}.`);
  }
  return message;
}
