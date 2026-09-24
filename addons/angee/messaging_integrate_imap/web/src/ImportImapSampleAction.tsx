import { useMessagingT } from "@angee/messaging";
import { keysetFeedRows, useAuthoredMutation } from "@angee/refine";
import { Alert, Button, Checkbox, ControlBandProvider, DialogForm, FieldRoot, FieldRow, Input, RecordActionTrigger, ResourceViewProvider, RowsListView, errorMessage, useRecordChromeContext, useResourceView, type ListColumn } from "@angee/ui";
import type { DocumentType } from "@angee/gql/console";
import * as React from "react";

import { ImportImapSample } from "./documents";
import { samplePreviewCursor, useSamplePreviewFeed, type SampleRow } from "./sample-preview-feed";

type Outcome = DocumentType<typeof ImportImapSample>["import_imap_sample"];

function isoDate(offsetDays = 0): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

/** Operator-selected historical import. The server owns IMAP safety and admission. */
export function ImportImapSampleAction(): React.ReactElement {
  return <ResourceViewProvider scope="local" initialState={{ pageSize: 50 }}>
    <ImportImapSampleDialog />
  </ResourceViewProvider>;
}

function ImportImapSampleDialog(): React.ReactElement | null {
  const t = useMessagingT();
  const { recordId, record } = useRecordChromeContext();
  const { clearSelectedIds, setPage } = useResourceView();
  const [open, setOpen] = React.useState(false);
  const [mailbox, setMailbox] = React.useState("INBOX");
  const [since, setSince] = React.useState(() => isoDate(-30));
  const [before, setBefore] = React.useState(() => isoDate(1));
  const [allDates, setAllDates] = React.useState(false);
  const [limit, setLimit] = React.useState(20);
  const [previewStarted, setPreviewStarted] = React.useState(false);
  const [outcome, setOutcome] = React.useState<Outcome | null>(null);
  const [localError, setLocalError] = React.useState<string | null>(null);
  const { query: previewFeed, restart } = useSamplePreviewFeed({
    id: recordId, mailbox: mailbox.trim(), since: allDates ? null : since,
    before: allDates ? null : before, allDates, limit,
  });
  const [runImport, importState] = useAuthoredMutation(ImportImapSample, { invalidateModels: ["messaging.Message"] });

  if (record === null) return null;
  const busy = previewFeed.isFetching || importState.fetching;
  const operationError = (previewStarted ? previewFeed.error : null) ?? importState.error;
  const rows = keysetFeedRows(previewStarted ? previewFeed.data : undefined, (left, right) => right.uid - left.uid);
  const cursor = previewStarted ? previewFeed.data?.pages.at(-1)?.through : null;
  const preview = cursor ? samplePreviewCursor(cursor) : null;
  const columns: readonly ListColumn<SampleRow>[] = [
    { field: "subject", header: t("channel.imap.sample.subject"), render: (row) => row.subject || t("channel.imap.sample.noSubject") },
    { field: "sender", header: t("channel.imap.sample.sender") },
    { field: "sent_at", header: t("channel.imap.sample.sent") },
    { field: "size", header: t("channel.imap.sample.bytes"), align: "right" },
    { field: "flags", header: t("channel.imap.sample.flags"), render: (row) => row.flags.join(", ") },
  ];

  const invalidatePreview = (): void => {
    setPreviewStarted(false); setOutcome(null); setLocalError(null);
    // Resetting a pending mutation detaches its observer without cancelling the
    // import. Keep its native busy state across dialog close/reopen.
    if (!importState.fetching) importState.reset();
    clearSelectedIds(); setPage(1);
  };

  const loadPreview = async (): Promise<void> => {
    setLocalError(null); setOutcome(null);
    importState.reset();
    const from = Date.parse(`${since}T00:00:00Z`); const to = Date.parse(`${before}T00:00:00Z`);
    if (!mailbox.trim() || (!allDates && (!Number.isFinite(from) || !Number.isFinite(to) || to <= from))) {
      setLocalError(t("channel.imap.sample.invalidRange")); return;
    }
    if (!allDates && (to - from) / 86_400_000 > 366) {
      setLocalError(t("channel.imap.sample.rangeTooLong")); return;
    }
    clearSelectedIds(); setPage(1); setPreviewStarted(true);
    try {
      await restart();
    } catch { /* Native query state renders the server error. */ }
  };

  const importSelected = async (selectedIds: ReadonlySet<string>, clear: () => void): Promise<void> => {
    if (!preview) return;
    setLocalError(null);
    try {
      const data = await runImport({ id: recordId, mailbox: mailbox.trim(), uidvalidity: preview.uidvalidity, uids: [...selectedIds].map(Number) });
      if (data?.import_imap_sample) { setOutcome(data.import_imap_sample); clear(); }
    } catch { /* Authored mutation state renders the server error. */ }
  };

  return <DialogForm
    open={open}
    onOpenChange={(nextOpen) => { setOpen(nextOpen); if (!nextOpen) invalidatePreview(); }}
    title={t("channel.imap.sample.title")}
    description={t("channel.imap.sample.description")}
    size="lg"
    trigger={
      <RecordActionTrigger disabled={record.lifecycle !== "PAUSED"}>
        {t("channel.imap.sample.button")}
      </RecordActionTrigger>
    }
    footer={<Button type="button" variant="primary" disabled={busy} onClick={() => void loadPreview()}>{previewFeed.isFetching ? t("channel.imap.sample.previewing") : t("channel.imap.sample.preview")}</Button>}>
      <FieldRow label={t("channel.imap.sample.mailbox")}><Input value={mailbox} disabled={busy} onChange={(event) => { setMailbox(event.target.value); invalidatePreview(); }} /></FieldRow>
      <FieldRoot><FieldRoot.Label>{t("channel.imap.sample.allDates")}</FieldRoot.Label><Checkbox checked={allDates} disabled={busy} onCheckedChange={(checked) => { setAllDates(checked); invalidatePreview(); }} /></FieldRoot>
      <FieldRow label={t("channel.imap.sample.since")}><Input type="date" value={since} disabled={busy || allDates} onChange={(event) => { setSince(event.target.value); invalidatePreview(); }} /></FieldRow>
      <FieldRow label={t("channel.imap.sample.before")}><Input type="date" value={before} disabled={busy || allDates} onChange={(event) => { setBefore(event.target.value); invalidatePreview(); }} /></FieldRow>
      <FieldRow label={t("channel.imap.sample.limit")}><Input type="number" min={1} max={50} value={limit} disabled={busy} onChange={(event) => {
        const parsed = event.target.valueAsNumber;
        setLimit(Number.isFinite(parsed) ? Math.max(1, Math.min(50, Math.trunc(parsed))) : 1);
        invalidatePreview();
      }} /></FieldRow>
      {localError || operationError ? <Alert className="col-span-full" tone="danger">{localError ?? errorMessage(operationError, t("channel.imap.sample.failed"))}</Alert> : null}
      {outcome ? <Alert
        className="col-span-full"
        tone={outcome.missing_uids.length || !outcome.flags_unchanged ? "warning" : "success"}
      >
        {t("channel.imap.sample.imported", {
          imported: outcome.imported_uids.length,
          missing: outcome.missing_uids.length,
        })}{" "}
        {outcome.flags_unchanged
          ? t("channel.imap.sample.flagsUnchanged")
          : t("channel.imap.sample.flagsChanged")}
      </Alert> : null}
      {preview ? <div className="col-span-full min-h-0 space-y-3">
        <Alert tone="info">{t("channel.imap.sample.count", {
          loaded: rows.length, total: preview.totalCount,
          uidvalidity: preview.uidvalidity, upperUid: preview.upperUid,
        })}</Alert>
        <ControlBandProvider host={undefined}>
          <RowsListView scope="inherit" rows={rows} columns={columns} selectable emptyContent={t("channel.imap.sample.empty")}
            bulkActions={(selectedIds, clear) => <Button size="sm" variant="primary" disabled={busy || selectedIds.size > 50}
              title={selectedIds.size > 50 ? t("channel.imap.sample.selectionTooLarge") : undefined}
              onClick={() => void importSelected(selectedIds, clear)}>{importState.fetching ? t("channel.imap.sample.importing") : t("channel.imap.sample.importSelected", { count: selectedIds.size })}</Button>} />
        </ControlBandProvider>
        {previewFeed.hasNextPage ? <Button type="button" variant="secondary" disabled={busy}
          onClick={() => void previewFeed.fetchNextPage()}>{previewFeed.isFetching ? t("channel.imap.sample.previewing") : t("channel.imap.sample.loadOlder")}</Button> : null}
      </div> : null}
    </DialogForm>;
}
