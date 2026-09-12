import { useMemo } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { senderDisplayName } from "@angee/parties";
import {
  Avatar,
  Button,
  Glyph,
  ListView,
  PageHeader,
  RelativeTime,
  Select,
  Tag,
  avatarInitials,
  useResourceView,
} from "@angee/ui";
import { RESULT_LENSES, resultLensForGroup } from "./contract";
import { InboxOrder, InboxClearFilters, useResultControls } from "./Controls";
import { resultSource } from "./sources";
import type { InboxMessageRow, InboxResultRow } from "./documents";
import { INBOX_MODELS, type InboxNavigation } from "./state";
import { InboxSelection } from "./documents";
import { useNexusT } from "../i18n";

export function InboxResultsPane({
  navigation,
  timezone,
}: {
  navigation: InboxNavigation;
  timezone: string;
}) {
  const t = useNexusT();
  const view = useResourceView();
  const selection = useAuthoredQuery(
    InboxSelection,
    { sender: navigation.sender, circle: navigation.circle },
    {
      models: INBOX_MODELS,
      enabled: Boolean(navigation.sender || navigation.circle),
    },
  );
  const group = view.state.groupStack[0];
  const lens = resultLensForGroup(navigation.lens, group);
  const groupField = group?.field ?? "";
  const controls = useResultControls(lens);
  const source = useMemo(
    () => resultSource({ navigation, timezone, lens, groupField, t }),
    [navigation, timezone, lens, groupField, t],
  );
  const columns = useMemo(
    () => [
      {
        field: "id",
        header: t("inbox.results"),
        headerVisuallyHidden: true,
        sortable: false,
        interactive: true,
        render: (row: InboxResultRow) => (
          <ResultRow row={row} navigation={navigation} />
        ),
      },
    ],
    [navigation, t],
  );
  return (
    <section
      aria-label={t("inbox.results")}
      className="flex h-full min-h-0 flex-col"
    >
      <PageHeader
        density="compact"
        title={t("inbox.title")}
        description={timezone}
        actions={
          navigation.sender || navigation.circle ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => navigation.select()}
            >
              {selection.data?.inbox_selection?.label ??
                selection.error?.message ??
                t("inbox.loading")}
              <Glyph name="x" />
            </Button>
          ) : null
        }
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <ListView
          presentation="workspace"
          tableLayout="fixed"
          headerVisibility="visually-hidden"
          selectable={false}
          resource="nexus.InboxResults"
          source={source}
          columns={columns}
          availableViews={["list"]}
          textFilterField="text"
          maxGroupDepth={1}
          defaultExpandedGroups="none"
          {...controls}
          toolbarActions={
            <>
              <Select
                size="sm"
                aria-label={t("inbox.resultLens")}
                className="w-36"
                value={lens}
                options={RESULT_LENSES.map((value) => ({
                  value,
                  label: t(`inbox.${value}`),
                }))}
                onValueChange={(value) => {
                  const selected = RESULT_LENSES.find((lens) => lens === value);
                  if (!selected) return;
                  navigation.changeResult(selected, view.state);
                }}
              />
              <InboxOrder />
              <InboxClearFilters />
            </>
          }
          onRowClick={(row) => navigation.read(row.message.id, row.part?.id)}
          emptyContent={{
            title: t("inbox.noResults"),
            description: t("inbox.noResultsHint"),
          }}
          renderGroupLabel={
            groupField === "by_conversation"
              ? ({ label, bucket }) => {
                  const target = bucket.key?.by_conversation;
                  return typeof target === "string" ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="min-w-0 justify-start px-0"
                      onClick={() =>
                        target.startsWith("thread:")
                          ? navigation.conversation(target.slice(7))
                          : navigation.read(target.slice(8))
                      }
                    >
                      <Glyph name="comments" />
                      <span className="truncate">{label}</span>
                      <Glyph name="chevron-right" />
                    </Button>
                  ) : (
                    label
                  );
                }
              : undefined
          }
        />
      </div>
    </section>
  );
}

function ResultRow({
  row,
  navigation,
}: {
  row: InboxResultRow;
  navigation: InboxNavigation;
}) {
  const t = useNexusT();
  if (!row.part)
    return (
      <div>
        <InboxMessagePreview message={row.message} />
        {row.message.thread ? (
          <div onClick={(event) => event.stopPropagation()}>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                navigation.conversation(row.message.thread!.id, row.message.id)
              }
            >
              {t("inbox.openConversation")}
              <Glyph name="chevron-right" />
            </Button>
          </div>
        ) : (
          <Tag>{t("inbox.standalone")}</Tag>
        )}
      </div>
    );
  return (
    <div className="space-y-2 whitespace-normal py-3">
      <div
        className="flex items-center gap-2"
        onClick={(event) => event.stopPropagation()}
      >
        <Glyph name={row.fragment ? "file" : "files"} />
        {row.file ? (
          <Button
            size="sm"
            variant="ghost"
            className="min-w-0 justify-start px-0"
            onClick={() => {
              navigation.patch({
                message: row.message.id,
                part: row.part?.id,
                tab: "preview",
              });
            }}
          >
            <span className="truncate">
              {row.part.name || row.file.filename}
            </span>
          </Button>
        ) : (
          <span className="font-medium">
            {row.fragment
              ? t("inbox.sharedText")
              : row.part.name || t("inbox.file")}
          </span>
        )}
        <Tag>{row.part.role}</Tag>
        {!row.file && !row.fragment ? (
          <Tag tone="warning">{t("inbox.fileUnavailable")}</Tag>
        ) : null}
        {row.file || row.fragment ? (
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            onClick={() => navigation.connect(row.id, row.message.id)}
          >
            <Glyph name="link" />
            {t("inbox.matchingUses", { count: row.count })}
          </Button>
        ) : null}
      </div>
      {row.fragment ? (
        <p className="line-clamp-4 whitespace-pre-wrap text-13 text-fg-muted">
          {row.fragment.text}
        </p>
      ) : null}
      <div className="text-2xs text-fg-muted">
        {t("inbox.sourceUse")}: {senderDisplayName(row.message.sender)} ·{" "}
        {row.message.channel?.display_name || row.message.platform} ·{" "}
        <RelativeTime value={row.latest} />
      </div>
    </div>
  );
}

export function InboxMessagePreview({ message }: { message: InboxMessageRow }) {
  const t = useNexusT();
  const sender = senderDisplayName(message.sender, t("inbox.unknownSender"));
  return (
    <div className="flex min-w-0 items-start gap-3 whitespace-normal py-3">
      <Avatar size="sm" initials={avatarInitials(sender)} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-medium">{sender}</span>
          {message.starred ? (
            <Glyph name="star" className="text-warning-text" />
          ) : null}
          <span className="ml-auto whitespace-nowrap text-2xs text-fg-muted">
            <RelativeTime value={message.sent_at ?? message.created_at} />
          </span>
        </div>
        {message.title ? (
          <p className="truncate text-13 font-medium">{message.title}</p>
        ) : null}
        <p className="line-clamp-2 text-13 text-fg-muted">{message.preview}</p>
        <div className="flex items-center gap-2 text-2xs text-fg-muted">
          <Tag>{message.platform}</Tag>
          <span>{message.channel?.display_name}</span>
        </div>
      </div>
    </div>
  );
}
