import { useMessagingT } from "@angee/messaging";
import { useAuthoredMutation } from "@angee/refine";
import { Alert, Button, Checkbox, ControlBandProvider, DialogForm, FieldRow, Input, RecordActionTrigger, RowsListView, errorMessage, useRecordChromeContext, type ListColumn } from "@angee/ui";
import type { DocumentType } from "@angee/gql/console";
import * as React from "react";

import { ImportImapSample, PreviewImapSample } from "./documents";

type SampleRow = { id: string; uid: number; subject: string; sender: string; sentAt: string; size: number; flags: string };
type Preview = DocumentType<typeof PreviewImapSample>["preview_imap_sample"];
type Outcome = DocumentType<typeof ImportImapSample>["import_imap_sample"];

const columns: readonly ListColumn<SampleRow>[] = [
  { field: "subject", header: "Subject" },
  { field: "sender", header: "Sender" },
  { field: "sentAt", header: "Sent" },
  { field: "size", header: "Bytes", align: "right" },
  { field: "flags", header: "Flags" },
];

function isoDate(offsetDays = 0): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

/** Operator-selected historical import. The server remains the owner of IMAP safety and admission. */
export function ImportImapSampleAction(): React.ReactElement | null {
  const t = useMessagingT();
  const { recordId, record } = useRecordChromeContext();
  const [open, setOpen] = React.useState(false);
  const [mailbox, setMailbox] = React.useState("INBOX");
  const [since, setSince] = React.useState(() => isoDate(-30));
  const [before, setBefore] = React.useState(() => isoDate(1));
  const [allDates, setAllDates] = React.useState(false);
  const [limit, setLimit] = React.useState(20);
  const [preview, setPreview] = React.useState<Preview | null>(null);
  const [previewGeneration, setPreviewGeneration] = React.useState(0);
  const [outcome, setOutcome] = React.useState<Outcome | null>(null);
  const [localError, setLocalError] = React.useState<string | null>(null);
  const [runPreview, previewState] = useAuthoredMutation(PreviewImapSample);
  const [runImport, importState] = useAuthoredMutation(ImportImapSample, { invalidateModels: ["messaging.Message"] });

  if (record === null) return null;
  const busy = previewState.fetching || importState.fetching;
  const operationError = previewState.error ?? importState.error;
  const rows: readonly SampleRow[] = (preview?.messages ?? []).map((message) => ({
    id: String(message.uid), uid: message.uid,
    subject: message.subject || t("channel.imap.sample.noSubject"), sender: message.sender,
    sentAt: message.sent_at, size: message.size, flags: message.flags.join(", "),
  }));

  const loadPreview = async (continuation?: Pick<Preview, "uidvalidity" | "upper_uid" | "next_before_uid">): Promise<void> => {
    setLocalError(null); setOutcome(null);
    const from = Date.parse(`${since}T00:00:00Z`); const to = Date.parse(`${before}T00:00:00Z`);
    if (!mailbox.trim() || (!allDates && (!Number.isFinite(from) || !Number.isFinite(to) || to <= from))) {
      setLocalError(t("channel.imap.sample.invalidRange")); return;
    }
    if (!allDates && (to - from) / 86_400_000 > 366) {
      setLocalError(t("channel.imap.sample.rangeTooLong")); return;
    }
    try {
      const data = await runPreview({
        id: recordId,
        mailbox: mailbox.trim(),
        since: allDates ? null : since,
        before: allDates ? null : before,
        allDates,
        uidvalidity: continuation?.uidvalidity,
        upperUid: continuation?.upper_uid,
        beforeUid: continuation?.next_before_uid,
        limit: Math.max(1, Math.min(50, limit)),
      });
      const page = data?.preview_imap_sample ?? null;
      if (!continuation) setPreviewGeneration((current) => current + 1);
      setPreview((current) => continuation && current && page ? {
        ...page,
        messages: [...current.messages, ...page.messages],
      } : page);
    } catch { /* Authored mutation state renders the server error. */ }
  };

  const importSelected = async (selectedIds: ReadonlySet<string>, clear: () => void): Promise<void> => {
    if (!preview) return;
    setLocalError(null);
    try {
      const data = await runImport({ id: recordId, mailbox: preview.mailbox, uidvalidity: preview.uidvalidity, uids: [...selectedIds].map(Number) });
      if (data?.import_imap_sample) { setOutcome(data.import_imap_sample); clear(); }
    } catch { /* Authored mutation state renders the server error. */ }
  };

  return <DialogForm
    open={open}
    onOpenChange={setOpen}
    title={t("channel.imap.sample.title")}
    description={t("channel.imap.sample.description")}
    size="lg"
    trigger={
      <RecordActionTrigger disabled={record.lifecycle !== "PAUSED"}>
        {t("channel.imap.sample.button")}
      </RecordActionTrigger>
    }
      footer={<Button type="button" variant="primary" disabled={busy} onClick={() => void loadPreview()}>{previewState.fetching ? t("channel.imap.sample.previewing") : t("channel.imap.sample.preview")}</Button>}>
      <FieldRow label={t("channel.imap.sample.mailbox")}><Input value={mailbox} onChange={(event) => { setMailbox(event.target.value); setPreview(null); }} /></FieldRow>
      <FieldRow label={t("channel.imap.sample.scope")}><Checkbox checked={allDates} onCheckedChange={(checked) => { setAllDates(checked); setPreview(null); }}>{t("channel.imap.sample.allDates")}</Checkbox></FieldRow>
      <FieldRow label={t("channel.imap.sample.since")}><Input type="date" value={since} disabled={allDates} onChange={(event) => { setSince(event.target.value); setPreview(null); }} /></FieldRow>
      <FieldRow label={t("channel.imap.sample.before")}><Input type="date" value={before} disabled={allDates} onChange={(event) => { setBefore(event.target.value); setPreview(null); }} /></FieldRow>
      <FieldRow label={t("channel.imap.sample.limit")}><Input type="number" min={1} max={50} value={limit} onChange={(event) => setLimit(Number(event.target.value))} /></FieldRow>
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
          loaded: preview.messages.length, total: preview.total_count,
          uidvalidity: preview.uidvalidity, upperUid: preview.upper_uid,
        })}</Alert>
        <ControlBandProvider host={undefined}>
          <RowsListView key={previewGeneration} scope="local" rows={rows} columns={columns} pageSize={50} selectable emptyContent={t("channel.imap.sample.empty")}
            bulkActions={(selectedIds, clear) => <Button size="sm" variant="primary" disabled={busy || !selectedIds.size || selectedIds.size > 50}
              title={selectedIds.size > 50 ? t("channel.imap.sample.selectionTooLarge") : undefined}
              onClick={() => void importSelected(selectedIds, clear)}>{importState.fetching ? t("channel.imap.sample.importing") : t("channel.imap.sample.importSelected", { count: selectedIds.size })}</Button>} />
        </ControlBandProvider>
        {preview.next_before_uid ? <Button type="button" variant="secondary" disabled={busy}
          onClick={() => void loadPreview(preview)}>{previewState.fetching ? t("channel.imap.sample.previewing") : t("channel.imap.sample.loadOlder")}</Button> : null}
      </div> : null}
    </DialogForm>;
}
