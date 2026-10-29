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

export const INTEGRATION_RETRY_DISCOVERY_ACTION_ID = "integrate.connection.retryDiscovery";
export const INTEGRATION_TEST_CONNECTION_ACTION_ID = "integrate.connection.test";
export const INTEGRATION_PAUSE_ACTION_ID = "integrate.lifecycle.pause";
export const INTEGRATION_RESUME_ACTION_ID = "integrate.lifecycle.resume";
export const INTEGRATION_DISCONNECT_ACTION_ID = "integrate.lifecycle.disconnect";

/** Credential-health facts shared by lifecycle and OAuth actions. */
export const INTEGRATION_CONNECTION_FIELDS = [
  "credential_status", "is_reconnect_required", "discovery_ready", "discovery_pending",
] as const;

/** Lifecycle tokens owned by integrate's transition vocabulary. */
export const INTEGRATION_LIFECYCLE_TOKENS = [
  "connected",
  "paused",
  "disconnected",
] as const;
export type IntegrationLifecycleToken =
  (typeof INTEGRATION_LIFECYCLE_TOKENS)[number];

/** Eligibility projected from the model's guarded Disconnect verb. */
export const isConnectedOrPaused = ({ record }: ConditionalMutationButtonContext): boolean =>
  record.can_disconnect === true;

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
      when={({ record }) => record.can_pause === true}
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
export function RetryDiscoveryAction(): React.ReactElement {
  const t = useIntegrateT();
  return <ConditionalMutationButton field="retry_discovery" label={t("connection.retryDiscovery")}
    when={({ record }) => record.can_retry_discovery === true} />;
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
