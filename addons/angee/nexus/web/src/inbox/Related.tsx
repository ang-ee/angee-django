import { useMemo } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { senderDisplayName } from "@angee/parties";
import {
  Alert,
  Button,
  Dialog,
  PreviewPane,
  EmptyState,
  Glyph,
  ListView,
  PageHeader,
  Tag,
} from "@angee/ui";
import { InboxRelatedTarget, type InboxConnectionRow } from "./documents";
import { InboxOrder } from "./Controls";
import { InboxMessagePreview } from "./Results";
import { relatedSource } from "./sources";
import { INBOX_MODELS, type InboxNavigation } from "./state";
import { useNexusT } from "../i18n";

/** Related owns a pinned target and independent native collection; no main scope enters here. */
export function InboxRelatedPane({
  navigation,
}: {
  navigation: InboxNavigation;
}) {
  const t = useNexusT();
  const query = useAuthoredQuery(
    InboxRelatedTarget,
    { target: navigation.related, source: navigation.relatedFrom },
    { models: INBOX_MODELS, enabled: Boolean(navigation.related) },
  );
  const target = query.data?.inbox_related_target;
  const source = useMemo(
    () => relatedSource(navigation.related, target?.unit ?? "messages", t),
    [navigation.related, target?.unit, t],
  );
  const columns = useMemo(
    () => [
      {
        field: "id",
        header: t("inbox.related"),
        headerVisuallyHidden: true,
        sortable: false,
        interactive: true,
        render: (row: InboxConnectionRow) => (
          <ConnectionRow row={row} navigation={navigation} />
        ),
      },
    ],
    [navigation, t],
  );
  return (
    <section
      className="flex h-full min-h-0 flex-col"
      aria-label={t("inbox.related")}
    >
      <PageHeader
        density="compact"
        headingLevel={2}
        title={t("inbox.related")}
        actions={
          <Button
            size="iconSm"
            variant="ghost"
            aria-label={t("inbox.closeRelated")}
            onClick={navigation.closeRelated}
          >
            <Glyph name="x" />
          </Button>
        }
      />
      {!navigation.related ? (
        <EmptyState
          icon="link"
          title={t("inbox.related")}
          description={t("inbox.relatedHint")}
        />
      ) : (
        <>
          {query.error ? (
            <Alert tone="danger">{query.error.message}</Alert>
          ) : null}
          <div className="space-y-2 border-b border-border-subtle p-4">
            {target?.file?.url ? (
              <Dialog.Root key={target.id}>
                <Dialog.Trigger
                  render={
                    <Button
                      size="sm"
                      variant="ghost"
                      className="max-w-full justify-start px-0"
                    />
                  }
                >
                  <span className="truncate">{target.label}</span>
                </Dialog.Trigger>
                <Dialog.Portal>
                  <Dialog.Backdrop />
                  <Dialog.Content size="lg" placement="center">
                    <Dialog.Header className="flex items-center justify-between">
                      <Dialog.Title>{target.label}</Dialog.Title>
                      <Dialog.Close />
                    </Dialog.Header>
                    <Dialog.Body>
                      <PreviewPane
                        file={{
                          url: target.file.url,
                          name: target.label,
                          mime: target.file.mime_type?.mime_type,
                          size: target.file.size_bytes,
                        }}
                      />
                    </Dialog.Body>
                  </Dialog.Content>
                </Dialog.Portal>
              </Dialog.Root>
            ) : (
              <p className="text-13 font-semibold">
                {target?.label ?? t("inbox.loading")}
              </p>
            )}
            {target?.fragment ? (
              <p className="line-clamp-4 whitespace-pre-wrap text-13 text-fg-muted">
                {target.fragment.text}
              </p>
            ) : null}
            {target ? (
              <p className="text-2xs text-fg-muted">
                {t("inbox.accessibleCount", {
                  count: target.count,
                  unit: target.unit,
                })}
              </p>
            ) : null}
            {target?.source ? (
              <Button
                size="sm"
                variant="ghost"
                className="max-w-full justify-start px-0"
                onClick={() =>
                  navigation.read(target.source!.id, target.part?.id)
                }
              >
                <Glyph name="chevron-left" />
                <span className="truncate">
                  {t("inbox.source")}: {senderDisplayName(target.source.sender)}
                </span>
              </Button>
            ) : null}
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <ListView
              resource="nexus.InboxConnections"
              source={source}
              columns={columns}
              availableViews={["list"]}
              textFilterField="text"
              groupOptions={[]}
              filterOptions={[]}
              customFilterFields={[]}
              toolbarActions={<InboxOrder />}
              onRowClick={(row) => {
                if (row.message) navigation.read(row.message.id, row.part?.id);
              }}
              emptyContent={t("inbox.noRelated")}
            />
          </div>
        </>
      )}
    </section>
  );
}

function ConnectionRow({
  row,
  navigation,
}: {
  row: InboxConnectionRow;
  navigation: InboxNavigation;
}) {
  const t = useNexusT();
  if (row.message)
    return (
      <div>
        {row.message.id === navigation.relatedFrom ? (
          <Tag tone="brand">{t("inbox.source")}</Tag>
        ) : null}
        <InboxMessagePreview message={row.message} />
        {row.message.thread ? (
          <div onClick={(event) => event.stopPropagation()}>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                navigation.conversation(
                  row.message!.thread!.id,
                  row.message!.id,
                )
              }
            >
              {t("inbox.openConversation")}
            </Button>
          </div>
        ) : null}
      </div>
    );
  if (row.handle)
    return (
      <div className="space-y-1 whitespace-normal py-3">
        <Tag>{row.role}</Tag>
        <p className="text-13 font-medium">{row.label}</p>
        <p className="text-2xs text-fg-muted">{row.handle.value}</p>
      </div>
    );
  if (row.src && row.dst)
    return (
      <div className="space-y-2 whitespace-normal py-3">
        <Tag>{t(`inbox.relation.${row.label}`)}</Tag>
        <p className="text-2xs text-fg-muted">
          {row.provenance}
          {row.confidence !== null
            ? ` · ${Math.round(row.confidence * 100)}%`
            : ""}
        </p>
        {[row.src, row.dst].map((message, index) => (
          <Button
            key={`${index}:${message.id}`}
            size="sm"
            variant="ghost"
            className="h-auto w-full justify-start whitespace-normal text-left"
            onClick={() => navigation.read(message.id)}
          >
            <Glyph name={index === 0 ? "arrow-up-right" : "arrow-down"} />
            <span>
              {senderDisplayName(message.sender)} ·{" "}
              {message.title || message.preview}
            </span>
          </Button>
        ))}
      </div>
    );
  return (
    <div className="py-3">
      <Tag>{row.role}</Tag> {row.label}
    </div>
  );
}
