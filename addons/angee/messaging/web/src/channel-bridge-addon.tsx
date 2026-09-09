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

/**
 * One vendor-owned record verb on the channel form, scoped by the addon to the
 * vendor's own rows (the impl-keyed record-actions slot). The vendor renders the
 * verb itself — typically `ConditionalMutationButton` over its own generated
 * action, with `args` when the verb collects input (re-entering a login).
 */
export interface ChannelRecordAction {
  /** Stable contribution id, unique across the channel record-actions slot. */
  id: string;
  /** Order among the record verbs; messaging's shared lifecycle verbs sit at 10–14. */
  sequence: number;
  content: ReactNode;
}

export interface ChannelPollBridgeAddonOptions {
  /** Stable addon id; also owns the connect contribution id. */
  id: string;
  /** Channel backend registry key. */
  key: string;
  /** Toolbar contribution order. */
  sequence: number;
  /** Vendor-owned channel creation action. */
  connectAction: ReactNode;
  /** Explicit messaging-namespace contribution, including vendor menu copy. */
  i18n: { messaging: Record<string, string> };
  /** Vendor-owned record verbs, rendered only on this backend's channel rows. */
  recordActions?: readonly ChannelRecordAction[];
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
  recordActions = [],
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
    i18n,
    menus: [channelBridgeMenu(i18n.messaging, key)],
    slots: [
      channelBridgeConnectSlot(id, sequence, connectAction),
      {
        ...channelActions,
        id: connectActionId,
        sequence: 10,
        content: pairingAction("disconnected", "channel.pairing.connect", true),
      },
      {
        ...channelActions,
        id: pairingActionId,
        sequence: 10,
        content: pairingAction("connected", "channel.pairing.status"),
      },
      {
        ...channelActions,
        id: INTEGRATION_RESUME_ACTION_ID,
        sequence: 12,
        content: pairingAction("paused", "channel.pairing.resume", true),
      },
      {
        ...channelActions,
        id: INTEGRATION_DISCONNECT_ACTION_ID,
        sequence: 13,
        content: disconnectAction,
      },
      ...channelRecordActionSlots(key, recordActions),
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
  recordActions = [],
}: ChannelPollBridgeAddonOptions): BaseAddon {
  return defineBaseAddon({
    id,
    i18n,
    menus: [channelBridgeMenu(i18n.messaging, key)],
    slots: [
      channelBridgeConnectSlot(id, sequence, connectAction),
      ...channelRecordActionSlots(key, recordActions),
    ],
  });
}

/** Scope each vendor record verb to the vendor's own channel rows. */
function channelRecordActionSlots(key: string, recordActions: readonly ChannelRecordAction[]) {
  const target = formViewRecordActionsSlot(CHANNEL_MODEL, key);
  return recordActions.map((action) => ({ ...target, ...action }));
}

/** Emit one vendor entry under Messaging. */
function channelBridgeMenu(i18n: Record<string, string>, key: string) {
  return {
    id: `messaging.${key}`,
    label: vendorMenuMessage(i18n, key, "label"),
    route: "messaging.channels",
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
