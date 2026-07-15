import type { Row } from "@angee/metadata";
import { optionToken } from "@angee/ui";
import * as React from "react";

import {
  ConditionalMutationButton,
  type ConditionalMutationButtonContext,
} from "./ConditionalMutationButton";
import { useIntegrateT } from "./i18n";

/**
 * The MTI parent every integration subtype reports as its canonical model.
 * These verbs are contributed against it once, so each subtype's form inherits
 * them without this addon naming a subtype it does not own.
 */
export const INTEGRATION_MODEL = "integrate.Integration";

export const INTEGRATION_PAUSE_ACTION_ID = "integrate.lifecycle.pause";
export const INTEGRATION_RESUME_ACTION_ID = "integrate.lifecycle.resume";
export const INTEGRATION_DISCONNECT_ACTION_ID = "integrate.lifecycle.disconnect";

// These mirror the `source=` sets declared on the model's `@transition`s
// (`Integration.pause`, `.connect`, `.disconnect`). The declaration is the owner
// and this copy drifts silently if it changes; both collapse into one fact once
// an action registry emits each transition's source set into the metadata
// artifact — the seam `integrate/schema.py` anticipates for these same verbs.
const isConnected = lifecycleIs("connected");

/**
 * Rows the shared Disconnect reaches — integrate's lifecycle vocabulary, exported
 * so a vendor specializing Disconnect gates on the same set instead of re-spelling
 * it (and drifting from `Integration.disconnect`'s declared `source=`).
 */
export const isConnectedOrPaused = ({
  record,
}: ConditionalMutationButtonContext): boolean =>
  ["connected", "paused"].includes(integrationLifecycle(record));

/**
 * Rows Resume reaches. `Integration.connect` declares
 * `source=[DISCONNECTED, PAUSED]`, so a *disconnected* row that still holds its
 * credential can honestly reconnect. Gating on `paused` alone left it stranded:
 * Disconnect reaches every subtype, but a vendor's own Connect authors a **new**
 * row (IMAP's `connect_imap_channel`, CardDAV's directory connect both
 * `create(...)`), so a disconnected row was unreachable from the console with its
 * credential still attached.
 *
 * A row with no credential has nothing to reconnect *with* — its vendor's Connect
 * authors one, so Resume stays hidden. A form that does not select `credential`
 * reads as having none: Resume stays paused-only there rather than offering a
 * reconnect it cannot substantiate.
 */
const canResume = (context: ConditionalMutationButtonContext): boolean => {
  const lifecycle = integrationLifecycle(context.record);
  return (
    lifecycle === "paused" ||
    (lifecycle === "disconnected" && context.record.credential != null)
  );
};

/**
 * Pause a connected integration while retaining its configuration.
 *
 * Connecting is deliberately absent: it means a real handshake for every subtype
 * that has credentials (an OAuth exchange, a CardDAV login, a WhatsApp pairing),
 * and the addon that owns the vendor owns that UX. `mark_integration_connected`
 * is a credential-free flag flip, correct only as the inverse of a pause — so it
 * backs Resume below and nothing else.
 */
export function PauseIntegrationAction(): React.ReactElement {
  const t = useIntegrateT();
  return (
    <ConditionalMutationButton
      field="pause_integration"
      label={t("lifecycle.pause")}
      when={isConnected}
    />
  );
}

/** Resume a paused — or credentialed but disconnected — integration. */
export function ResumeIntegrationAction(): React.ReactElement {
  const t = useIntegrateT();
  return (
    <ConditionalMutationButton
      field="mark_integration_connected"
      label={t("lifecycle.resume")}
      when={canResume}
      variant="primary"
    />
  );
}

/** Disconnect a running or paused integration. */
export function DisconnectIntegrationAction(): React.ReactElement {
  const t = useIntegrateT();
  return (
    <ConditionalMutationButton
      field="mark_integration_disconnected"
      label={t("lifecycle.disconnect")}
      when={isConnectedOrPaused}
      variant="danger"
      confirm={{
        title: t("lifecycle.disconnectConfirm.title"),
        body: t("lifecycle.disconnectConfirm.body"),
        danger: true,
      }}
    />
  );
}

/**
 * One integration row's lifecycle as the token the model's `@transition`s
 * declare. Exported because integrate owns the lifecycle vocabulary: an addon
 * specializing a lifecycle verb for its own vendor reads the row through this
 * rather than re-spelling the enum-read casing rule (see `optionToken`).
 */
export function integrationLifecycle(record: Row): string {
  return optionToken(record.lifecycle);
}

function lifecycleIs(
  expected: string,
): (context: ConditionalMutationButtonContext) => boolean {
  return ({ record }) => integrationLifecycle(record) === expected;
}
