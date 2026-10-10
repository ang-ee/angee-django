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

export const INTEGRATION_RETRY_BINDING_ACTION_ID = "integrate.connection.retryDiscovery";
export const INTEGRATION_TEST_CONNECTION_ACTION_ID = "integrate.connection.test";
export const INTEGRATION_PAUSE_ACTION_ID = "integrate.lifecycle.pause";
export const INTEGRATION_RESUME_ACTION_ID = "integrate.lifecycle.resume";
export const INTEGRATION_DISCONNECT_ACTION_ID = "integrate.lifecycle.disconnect";

/** Credential-health facts shared by lifecycle and OAuth actions. */
export const INTEGRATION_CONNECTION_FIELDS = [
  "credential_status", "is_reconnect_required", "binding_ready", "binding_pending",
] as const;

/** Lifecycle tokens owned by integrate's transition vocabulary. */
export const INTEGRATION_LIFECYCLE_TOKENS = [
  "connected",
  "paused",
  "disconnected",
] as const;
export type IntegrationLifecycleToken =
  (typeof INTEGRATION_LIFECYCLE_TOKENS)[number];

// These mirror the `source=` sets declared on the model's `@transition`s
// (`Integration.pause`, `.connect`, `.disconnect`). The declaration is the owner
// and this copy drifts silently if it changes; both collapse into one fact once
// an action registry emits each transition's source set into the metadata
// artifact — the seam `integrate/schema.py` anticipates for these same verbs.
const isConnected = integrationLifecycleIs("connected");

/**
 * Rows the shared Disconnect reaches — integrate's lifecycle vocabulary, exported
 * so a vendor specializing Disconnect gates on the same set instead of re-spelling
 * it (and drifting from `Integration.disconnect`'s declared `source=`).
 */
export const isConnectedOrPaused = ({
  record,
}: ConditionalMutationButtonContext): boolean =>
  ["connected", "paused"].includes(integrationLifecycle(record));

/** Read the shared credential projection on the parent and its capabilities. */
export function integrationHasCredential(record: Row): boolean {
  return Boolean(record.credential_status);
}

/**
 * Exercise a credentialed integration's connection and toast the server's answer.
 *
 * The backend dispatches to the concrete capability (`test_connection`): a
 * channel backend performs its real login, a parent-only integration proves
 * its credential. Reaches the same rows Disconnect does — the ones holding a
 * credential worth testing.
 */
export function TestConnectionAction(): React.ReactElement {
  const t = useIntegrateT();
  return (
    <ConditionalMutationButton
      field="test_connection"
      label={t("connection.test")}
      glyph="circle-check"
      when={isConnectedOrPaused}
    />
  );
}

/**
 * Pause a connected integration while retaining its configuration.
 *
 * Connecting is deliberately absent: it means a real handshake for every subtype
 * that has credentials (an OAuth exchange, a CardDAV login, a WhatsApp pairing),
 * and the addon that owns the vendor owns that UX. `mark_integration_connected`
 * resumes the existing connection and repeats discovery through its model owner.
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
      when={({ record }) => record.can_resume === true}
      variant="primary"
    />
  );
}

/** Reset failed discovery through the integration's explicit recovery verb. */
export function RetryBindingAction(): React.ReactElement {
  const t = useIntegrateT();
  return <ConditionalMutationButton field="retry_binding" label={t("connection.retryDiscovery")}
    when={({ record }) => record.can_retry_binding === true} />;
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

/** Return a record-chrome predicate using integrate's lifecycle token owner. */
export function integrationLifecycleIs(
  expected: IntegrationLifecycleToken,
): (context: ConditionalMutationButtonContext) => boolean {
  return ({ record }) => integrationLifecycle(record) === expected;
}
