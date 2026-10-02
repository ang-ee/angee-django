import * as React from "react";
import { Field, Form, Group, registerForm, type RegisteredFormProps } from "@angee/ui";
import { IntegrationSyncFields } from "@angee/integrate";

import { usePostsT } from "./i18n";

const FEED_MODEL = "posts.Feed";

function FeedForm({ resource: _resource, ...props }: RegisteredFormProps): React.ReactElement {
  const t = usePostsT();
  return (
    <Form {...props} resource={FEED_MODEL}>
      <Field name="display_name" label={t("feed.name")} title readOnly />
      <Group label={t("feed.details")} columns={2}>
        <Field name="feed_backend_class" label={t("feed.backend")} readOnly />
        <Field name="external_id" label={t("feed.externalId")} readOnly />
        <Field name="handle" label={t("feed.handle")} readOnly />
        <Field name="lifecycle" label={t("feed.lifecycle")} readOnly />
        <Field name="runtime_status" label={t("feed.runtime")} readOnly />
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

export const feedForm = registerForm(FEED_MODEL, FeedForm);
