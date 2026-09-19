import { useMessagingT } from "@angee/messaging";
import { useAuthoredMutation } from "@angee/refine";
import { Alert, Button, ControlBandProvider, DialogForm, FieldRow, Input, RecordActionTrigger, RowsListView, errorMessage, useRecordChromeContext, type ListColumn } from "@angee/ui";
import * as React from "react";

import { ImportImapSample, PreviewImapSample } from "./documents";

type SampleRow = { id: string; uid: number; subject: string; sender: string; sentAt: string; size: number; flags: string };
type Preview = { mailbox: string; uidvalidity: number; truncated: boolean; messages: readonly { uid: number; subject: string; sent_at: string; sender: string; size: number; flags: readonly string[] }[] };
type Outcome = { imported_uids: readonly number[]; missing_uids: readonly number[]; flags_unchanged: boolean };

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
  const [limit, setLimit] = React.useState(20);
  const [preview, setPreview] = React.useState<Preview | null>(null);
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

  const loadPreview = async (): Promise<void> => {
    setLocalError(null); setOutcome(null);
    const from = Date.parse(`${since}T00:00:00Z`); const to = Date.parse(`${before}T00:00:00Z`);
    if (!mailbox.trim() || !Number.isFinite(from) || !Number.isFinite(to) || to <= from) {
      setLocalError(t("channel.imap.sample.invalidRange")); return;
    }
    if ((to - from) / 86_400_000 > 366) {
      setLocalError(t("channel.imap.sample.rangeTooLong")); return;
    }
    try {
      const data = await runPreview({ id: recordId, mailbox: mailbox.trim(), since, before, limit: Math.max(1, Math.min(50, limit)) });
      setPreview(data?.preview_imap_sample ?? null);
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
      <FieldRow label={t("channel.imap.sample.mailbox")}><Input value={mailbox} onChange={(event) => setMailbox(event.target.value)} /></FieldRow>
      <FieldRow label={t("channel.imap.sample.since")}><Input type="date" value={since} onChange={(event) => setSince(event.target.value)} /></FieldRow>
      <FieldRow label={t("channel.imap.sample.before")}><Input type="date" value={before} onChange={(event) => setBefore(event.target.value)} /></FieldRow>
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
        {preview.truncated ? <Alert tone="info">{t("channel.imap.sample.truncated")}</Alert> : null}
        <ControlBandProvider host={undefined}>
          <RowsListView scope="local" rows={rows} columns={columns} pageSize={50} selectable emptyContent={t("channel.imap.sample.empty")}
            bulkActions={(selectedIds, clear) => <Button size="sm" variant="primary" disabled={busy} onClick={() => void importSelected(selectedIds, clear)}>{importState.fetching ? t("channel.imap.sample.importing") : t("channel.imap.sample.importSelected", { count: selectedIds.size })}</Button>} />
        </ControlBandProvider>
      </div> : null}
    </DialogForm>;
}
