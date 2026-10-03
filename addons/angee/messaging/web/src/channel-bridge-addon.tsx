import { defineBaseAddon } from "@angee/app";
import {
  ConditionalMutationButton,
  INTEGRATION_DISCONNECT_ACTION_ID,
  INTEGRATION_RESUME_ACTION_ID,
  integrationLifecycleIs,
  isConnectedOrPaused,
  type IntegrationLifecycleToken,
} from "@angee/integrate";
import type { ContainersDeclaration } from "@angee/ui/runtime";
import type { ReactNode } from "react";

import { CHANNEL_MODEL } from "./documents";
import { useMessagingT } from "./i18n";
import { ChannelPairingAction } from "./PairingDialog";

/**
 * One vendor-owned record verb in the channel form's overflow menu, shown only
 * on the vendor's own rows (`impl`). The vendor renders the verb itself —
 * typically `ConditionalMutationButton` over its own generated action, with
 * `args` when the verb collects input (re-entering a login).
 */
export interface ChannelRecordAction {
  /** Child id in the vendor addon's namespace (`<addon id>.<verb>`). */
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
  /** Explicit messaging-namespace contribution. */
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
}: ChannelBridgeAddonOptions) {
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
    containers: channelContainers(id, sequence, connectAction, key, recordActions, {
      // Pairing replaces Integration's resume and disconnect on this vendor's rows only.
      [`${id}.connect`]: { impl: key, sequence: 10, content: pairingAction("disconnected", "channel.pairing.connect", true) },
      [`${id}.pairing`]: { impl: key, sequence: 10, content: pairingAction("connected", "channel.pairing.status") },
      [`${id}.resume`]: {
        variant: { of: INTEGRATION_RESUME_ACTION_ID, impl: key },
        sequence: 12,
        content: pairingAction("paused", "channel.pairing.resume", true),
      },
    }, {
      [`${id}.disconnect`]: { variant: { of: INTEGRATION_DISCONNECT_ACTION_ID, impl: key }, sequence: 13, content: disconnectAction },
    }),
  });
}

/** Declare one poll channel vendor's connect and record-action contributions. */
export function defineChannelPollBridgeAddon({
  id,
  key,
  sequence,
  connectAction,
  i18n,
  recordActions = [],
}: ChannelPollBridgeAddonOptions) {
  return defineBaseAddon({
    id,
    i18n,
    containers: channelContainers(id, sequence, connectAction, key, recordActions),
  });
}

type ChannelActionChildren = Record<`${string}.${string}`, { content: ReactNode; sequence: number; impl?: string; variant?: { of: string; impl: string } }>;

/**
 * One vendor's channel children: its connect verb on the Channels toolbar, its
 * own form verbs, and its menu verbs scoped to its rows.
 */
function channelContainers(
  id: string,
  sequence: number,
  connectAction: ReactNode,
  key: string,
  recordActions: readonly ChannelRecordAction[],
  toolbar: ChannelActionChildren = {},
  menu: ChannelActionChildren = {},
): ContainersDeclaration {
  const vendorVerbs = Object.fromEntries(recordActions.map(({ id: verb, sequence: order, content }) => [verb, { impl: key, sequence: order, content }]));
  return {
    "messaging.channels#toolbar": { [`${id}.connect`]: { sequence, content: connectAction } },
    ...(Object.keys(toolbar).length ? { [`${CHANNEL_MODEL}#actions`]: toolbar } : {}),
    [`${CHANNEL_MODEL}#actions-menu`]: { ...menu, ...vendorVerbs },
  } as ContainersDeclaration;
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
