import * as React from "react";
import { Field, Form, Group, registerForm, useRecordChromeContext, useResourceRecordHref, useRouteHref, type RegisteredFormProps } from "@angee/ui";
import { ConnectIntegration, ConnectOAuthButton, IntegrationSyncFields } from "@angee/integrate";
import { useAuthoredMutation } from "@angee/refine";
import { useResourceInvalidates } from "@angee/metadata";

import { usePostsT } from "./i18n";

const FEED_MODEL = "posts.Feed";
const CONNECT_MODELS = [FEED_MODEL, "integrate.Integration"];

function FeedForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = usePostsT();
  return (
    <Form {...props} resource={FEED_MODEL}>
      <Field name="display_name" label={t("feed.name")} title createOnly />
      <Group label={t("feed.details")} columns={2}>
        <Field name="feed_backend_class" label={t("feed.backend")} createOnly />
        <Field name="external_id" label={t("feed.externalId")} readOnly />
        <Field name="handle" label={t("feed.handle")} readOnly />
        <Field name="lifecycle" label={t("feed.lifecycle")} readOnly />
        <Field name="runtime_status" label={t("feed.runtime")} readOnly />
        <Field name="reply_hold" label={t("feed.replyHold")} description={t("feed.replyHoldHelp")} />
      </Group>
      <Field name="config" label={t("feed.config")} readOnly />
      {IntegrationSyncFields({
        label: t("feed.sync"),
        fields: ["sync_stage", "last_sync_completed_at", "last_sync_items", "sync_progress", "sync_error"],
        labels: {
          sync_stage: t("feed.sync"),
          last_sync_completed_at: t("feed.syncedAt"),
          last_sync_items: t("feed.items"),
          sync_progress: t("feed.progress"),
          sync_error: t("feed.error"),
        },
      })}
    </Form>
  );
}

/** Saved-record Connect composes integrate's projection, document and browser flow. */
export function FeedConnectAction(): React.ReactElement | null {
  const context = useRecordChromeContext();
  const href = useResourceRecordHref(FEED_MODEL);
  const routeHref = useRouteHref();
  const invalidates = useResourceInvalidates(CONNECT_MODELS);
  const [connect] = useAuthoredMutation(ConnectIntegration, { invalidateModels: CONNECT_MODELS, invalidates });
  if (!context.record || context.formReadOnly) return null;
  return <ConnectOAuthButton row={context.record}
    disabled={context.actionsBlocked}
    next={href?.(context.recordId) ?? routeHref("posts.feeds")}
    start={async ({ id, redirectUri, next }) => (await connect({ resource: FEED_MODEL, id, redirectUri, next }))?.connect_integration}
  />;
}

export const feedForm = registerForm(FEED_MODEL, FeedForm);
