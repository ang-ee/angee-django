import * as React from "react";
import { Filter, holdsPermission, useResourceInvalidates } from "@angee/metadata";
import { useAuthoredMutation } from "@angee/refine";
import {
  Column, Facet, List, ResourceList, SelectionBarAction,
  useActionResultRun, useActionSelectionLimit, useConfirm, useResourceView,
  type StringIdRow,
} from "@angee/ui";

import { messageForm, messageRecordTabs } from "./MessageForm";
import { MESSAGE_SUMMARY_FIELDS, MessageSummary, type MessageSummaryData } from "./MessageSummary";
import { SendHeldDraftsDocument, DiscardHeldDraftsDocument } from "./documents";
import { useMessagingT } from "./i18n";

const MODEL = "messaging.Message";
const DEFAULT_GROUPS = { list: { field: "channel" } } as const;
const HELD_FILTER = { is_held: true } as const;
const ACTION_OPTIONS = { invalidateModels: [MODEL, "messaging.Thread"], errorNotification: false } as const;
export type MessageListRow = StringIdRow & MessageSummaryData & {
  revision?: number; permissions?: readonly string[];
};

/** Messages retain ordinary selection/delete; held verbs compose their own view. */
export function MessagesPage(): React.ReactElement {
  const t = useMessagingT();
  const view = useResourceView();
  const held = Filter.from(Filter.combineOptional(view.baseFilter, view.state.filter)).hasPreset(HELD_FILTER);
  const fields = held ? [...MESSAGE_SUMMARY_FIELDS, "revision", "permissions"] : MESSAGE_SUMMARY_FIELDS;
  const tabs = React.useMemo(() => messageRecordTabs(t), [t]);
  const filters = React.useMemo(() => [{ id: "held", label: t("messages.held"), filter: HELD_FILTER }], [t]);
  return <ResourceList<MessageListRow> resource={MODEL} form={messageForm} placement="inline" routed hideCreate recordTabs={tabs}>
    <List<MessageListRow> resource={MODEL} defaultGroups={DEFAULT_GROUPS}
      fields={fields} tableLayout="fixed" headerVisibility="visually-hidden"
      filterOptions={filters} selectable
      bulkActions={(ids, _clear, { filter, selectedRows }) => Filter.from(filter).hasPreset(HELD_FILTER)
        ? <HeldDraftActions ids={ids} rows={selectedRows} /> : null}
    >
      <Facet field="channel" label={t("messages.channel")} />
      <Column<MessageListRow> field="sent_at" header={t("messages.list")} hideable={false}
        render={(row) => <MessageSummary message={row} />} />
    </List>
  </ResourceList>;
}

/** Submit selection-owned revisions; drop settled ids so retries select fresh rows. */
export function HeldDraftActions({ ids, rows }: { ids: ReadonlySet<string>; rows: readonly MessageListRow[] }): React.ReactElement {
  const view = useResourceView();
  const t = useMessagingT();
  const confirm = useConfirm();
  const settle = useActionResultRun();
  const invalidates = useResourceInvalidates(ACTION_OPTIONS.invalidateModels);
  const [send] = useAuthoredMutation(SendHeldDraftsDocument, { ...ACTION_OPTIONS, invalidates });
  const [discard] = useAuthoredMutation(DiscardHeldDraftsDocument, { ...ACTION_OPTIONS, invalidates });
  const [busy, setBusy] = React.useState(false);
  const busyRef = React.useRef(false);
  const limit = useActionSelectionLimit();
  const available = rows.length === ids.size && rows.every((row) => holdsPermission(row, "send_held") && typeof row.revision === "number");
  const reason = limit === undefined ? t("messages.selectionLoading") : ids.size > limit
    ? t("messages.selectionLimit", { count: limit }) : !available ? t("messages.selectionUnavailable") : undefined;
  const reasonId = React.useId();
  const disabled = busy || reason !== undefined;
  const run = async (discarding: boolean): Promise<void> => {
    if (disabled || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      if (!await confirm({ title: t(discarding ? "messages.discardTitle" : "messages.sendTitle"),
        body: t(discarding ? "messages.discardBody" : "messages.sendBody", { count: ids.size }),
        confirm: t(discarding ? "messages.discard" : "messages.sendNow"), danger: discarding })) return;
      const selection = rows.flatMap((row) => typeof row.revision === "number" ? [{ id: row.id, expected_revision: row.revision }] : []);
      await settle(async () => {
        const data = discarding ? await discard({ selection }) : await send({ selection });
        const results = data && ("discard_held_drafts" in data ? data.discard_held_drafts : data.send_held_drafts);
        if (!results) return undefined;
        const succeeded = results.filter((outcome) => outcome.ok);
        const failures = results.filter((outcome) => !outcome.ok);
        view.setRowSelection((current) => {
          const next = { ...current };
          for (const outcome of results) if (outcome.id) delete next[outcome.id];
          return next;
        });
        return { ok: failures.length === 0,
          message: [t(discarding ? "messages.discarded" : "messages.queued", { count: succeeded.length }),
            ...failures.map((outcome) => outcome.message).filter(Boolean)].join("; ") };
      });
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };
  return <>
    <SelectionBarAction surface="brand" pending={busy} disabled={disabled}
      aria-describedby={reason ? reasonId : undefined}
      onClick={() => void run(false)}>{t("messages.sendNow")}</SelectionBarAction>
    <SelectionBarAction surface="brand" tone="danger" pending={busy} disabled={disabled}
      aria-describedby={reason ? reasonId : undefined}
      onClick={() => void run(true)}>{t("messages.discard")}</SelectionBarAction>
    {reason ? <span id={reasonId}>{reason}</span> : null}
  </>;
}
