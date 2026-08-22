import { senderDisplayName } from "@angee/parties";
import { useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import {
  Avatar,
  Column,
  EmptyState,
  ErrorBanner,
  Field,
  Form,
  Group,
  List,
  ListView,
  LoadingPanel,
  MessageFeed,
  MessagePartsView,
  MessageRow,
  ReactionBar,
  ResourceList,
  avatarInitials,
  errorMessage,
  reactionsFromGroups,
  type ListColumn,
  type RecordPanelContext,
  type RecordTabDescriptor,
  type StringIdRow,
} from "@angee/ui";

import {
  FEED_MESSAGE_MODELS,
  FeedMessagesDocument,
  type FeedMessageRow,
} from "./documents";
import { usePostsT, type PostsT } from "./i18n";
import { postsReactionCopy } from "./reaction-copy";

const FEED_MODEL = "posts.Feed";
const FEED_FOLLOW_MODEL = "posts.FeedFollow";
const FEED_POST_LIMIT = 50;

type FeedFollowRow = StringIdRow;

function FeedPostsTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = usePostsT();
  const variables = React.useMemo(
    () => ({ feedId: recordId, limit: FEED_POST_LIMIT }),
    [recordId],
  );
  const query = useAuthoredQuery(FeedMessagesDocument, variables, {
    enabled: recordId !== "",
    models: FEED_MESSAGE_MODELS,
  });
  if (query.fetching && !query.data) {
    return <LoadingPanel density="inline" message={t("feed.postsLoading")} />;
  }
  if (query.error) {
    return (
      <ErrorBanner
        title={t("feed.postsError")}
        description={errorMessage(query.error, t("feed.postsError"))}
      />
    );
  }

  const messages = query.data?.messages ?? [];
  if (messages.length === 0) {
    return <EmptyState icon="posts" title={t("feed.postsEmpty")} />;
  }

  return (
    <MessageFeed
      aria-describedby={undefined}
      busy={query.fetching}
      label={t("feed.postsLabel")}
      className="rounded-6 border border-border-subtle bg-sheet p-4"
    >
      {messages.map((message) => (
        <FeedPostRow key={message.id} message={message} t={t} />
      ))}
    </MessageFeed>
  );
}

function FeedPostRow({
  message,
  t,
}: {
  message: FeedMessageRow;
  t: PostsT;
}): React.ReactElement {
  const author = senderDisplayName(message.sender, t("post.author"));
  const timestamp = message.sent_at ?? message.created_at;
  const reactions = reactionsFromGroups(
    message.reaction_groups,
    postsReactionCopy(t),
  );
  const body = hasRenderableParts(message) ? (
    <MessagePartsView parts={message.parts} resolveFileUrl={(file) => file.url} />
  ) : (
    <span>{message.preview || t("post.emptyBody")}</span>
  );

  return (
    <MessageRow
      avatar={<Avatar initials={avatarInitials(author)} size="sm" />}
      author={author}
      timestamp={timestamp}
      reactions={
        reactions.length > 0 ? (
          <ReactionBar reactions={reactions} label={t("post.reactions")} />
        ) : null
      }
    >
      {body}
    </MessageRow>
  );
}

function hasRenderableParts(message: FeedMessageRow): boolean {
  return message.parts.some((part) => {
    const text = part.fragment?.text ?? "";
    return text.trim() !== "" || part.file !== null;
  });
}

function followColumns(t: PostsT): readonly ListColumn<FeedFollowRow>[] {
  return [
    { field: "handle.display_name", header: t("feed.follow.handle") },
    { field: "started_at", header: t("feed.follow.started") },
    { field: "ended_at", header: t("feed.follow.ended") },
    { field: "created_at", header: t("feed.follow.created") },
  ];
}

function FeedFollowsTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = usePostsT();
  const columns = React.useMemo(() => followColumns(t), [t]);
  return (
    <ListView<FeedFollowRow>
      resource={FEED_FOLLOW_MODEL}
      scope="local"
      fields={["id", "handle.display_name", "started_at", "ended_at", "created_at"]}
      baseFilter={{ feed: { exact: recordId } }}
      columns={columns}
      emptyContent={t("feed.follow.empty")}
    />
  );
}

function feedRecordTabs(t: PostsT): readonly RecordTabDescriptor[] {
  return [
    {
      id: "posts",
      label: t("feed.posts"),
      render: (context) => <FeedPostsTab {...context} />,
    },
    {
      id: "follows",
      label: t("feed.follows"),
      render: (context) => <FeedFollowsTab {...context} />,
    },
  ];
}

/** Public-content feeds compose the shared resource page and message-feed atoms. */
export function FeedsPage(): React.ReactElement {
  const t = usePostsT();
  const tabs = React.useMemo(() => feedRecordTabs(t), [t]);

  return (
    <ResourceList resource={FEED_MODEL} placement="inline" routed hideCreate recordTabs={tabs}>
      <List resource={FEED_MODEL}>
        <Column field="display_name" header={t("feed.name")} />
        <Column field="backend_class" header={t("feed.backend")} />
        <Column field="handle.display_name" header={t("feed.handle")} />
        <Column field="lifecycle" header={t("feed.lifecycle")} widget="statusBadge" />
        <Column field="runtime_status" header={t("feed.runtime")} widget="statusBadge" />
        <Column field="last_sync_status" header={t("feed.sync")} widget="statusBadge" />
        <Column field="last_sync_completed_at" header={t("feed.syncedAt")} />
      </List>
      <Form resource={FEED_MODEL}>
        <Field name="display_name" label={t("feed.name")} title readOnly />
        <Group label={t("feed.details")} columns={2}>
          <Field name="backend_class" label={t("feed.backend")} readOnly />
          <Field name="external_id" label={t("feed.externalId")} readOnly />
          <Field name="handle" label={t("feed.handle")} readOnly />
          <Field name="lifecycle" label={t("feed.lifecycle")} readOnly />
          <Field name="runtime_status" label={t("feed.runtime")} readOnly />
          <Field name="last_sync_status" label={t("feed.sync")} readOnly />
          <Field name="last_sync_completed_at" label={t("feed.syncedAt")} readOnly />
          <Field name="last_sync_items" label={t("feed.items")} readOnly />
        </Group>
        <Field name="config" label={t("feed.config")} readOnly />
        <Field name="sync_progress" label={t("feed.progress")} readOnly />
        <Field name="sync_error" label={t("feed.error")} readOnly />
      </Form>
    </ResourceList>
  );
}
