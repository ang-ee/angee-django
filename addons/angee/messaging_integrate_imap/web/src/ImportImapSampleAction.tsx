import { useMessagingT } from "@angee/messaging";
import { keysetFeedRows, useAuthoredMutation } from "@angee/refine";
import { Alert, Button, Checkbox, ControlBandProvider, DialogForm, FieldRoot, FieldRow, Input, RecordActionTrigger, ResourceViewProvider, RowsListView, errorMessage, useRecordChromeContext, useResourceView, type ListColumn } from "@angee/ui";
import type { DocumentType } from "@angee/gql/console";
import * as React from "react";
import { Controller, useForm, useWatch } from "react-hook-form";

import { ImportImapSample } from "./documents";
import { useSamplePreviewFeed, type SampleRow } from "./sample-preview-feed";

// The operations expose no limit metadata. The backend enforces this cap;
// test_messaging_imap pins the one exported UI bound to MAX_SAMPLE_MESSAGES.
export const IMAP_SAMPLE_LIMIT = 50;

type Outcome = DocumentType<typeof ImportImapSample>["import_imap_sample"];
type PreviewValues = { mailbox: string; since: string; before: string; allDates: boolean; limit: number };

function isoDate(offsetDays = 0): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

/** Operator-selected historical import. The server owns IMAP safety and admission. */
export function ImportImapSampleAction(): React.ReactElement {
  return <ResourceViewProvider scope="local" initialState={{ pageSize: IMAP_SAMPLE_LIMIT }}>
    <ImportImapSampleDialog />
  </ResourceViewProvider>;
}

function ImportImapSampleDialog(): React.ReactElement | null {
  const t = useMessagingT();
  const { recordId, record } = useRecordChromeContext();
  const { clearSelectedIds, setPage } = useResourceView();
  const [open, setOpen] = React.useState(false);
  const form = useForm<PreviewValues>({
    defaultValues: { mailbox: "INBOX", since: isoDate(-30), before: isoDate(1), allDates: false, limit: 20 },
  });
  const allDates = useWatch({ control: form.control, name: "allDates" });
  const [previewValues, setPreviewValues] = React.useState<PreviewValues | null>(null);
  const [outcome, setOutcome] = React.useState<Outcome | null>(null);
  const { query: previewFeed, restart } = useSamplePreviewFeed({
    id: recordId, mailbox: previewValues?.mailbox.trim() ?? "",
    since: previewValues?.allDates ? null : previewValues?.since,
    before: previewValues?.allDates ? null : previewValues?.before,
    allDates: previewValues?.allDates ?? false, limit: previewValues?.limit ?? IMAP_SAMPLE_LIMIT,
  });
  const [runImport, importState] = useAuthoredMutation(ImportImapSample, { invalidateModels: ["messaging.Message"] });

  // Only an explicit, validated submission starts the feed. Incomplete drafts
  // must not become query parameters, including an invalid page size.
  React.useEffect(() => {
    if (previewValues) void restart().catch(() => { /* Native query state renders the server error. */ });
  }, [previewValues, restart]);

  if (record === null) return null;
  const busy = previewFeed.isFetching || importState.fetching;
  const formError = Object.values(form.formState.errors).find((error) => error?.message)?.message;
  const operationError = (previewValues ? previewFeed.error : null) ?? importState.error;
  const rows = keysetFeedRows(previewValues ? previewFeed.data : undefined, (left, right) => right.uid - left.uid);
  const page = previewValues ? previewFeed.data?.pages.at(-1) : undefined;
  const preview = page?.metadata;
  const columns: readonly ListColumn<SampleRow>[] = [
    { field: "subject", header: t("channel.imap.sample.subject"), render: (row) => row.subject || t("channel.imap.sample.noSubject") },
    { field: "sender", header: t("channel.imap.sample.sender") },
    { field: "sent_at", header: t("channel.imap.sample.sent") },
    { field: "size", header: t("channel.imap.sample.bytes"), align: "right" },
    { field: "flags", header: t("channel.imap.sample.flags"), render: (row) => row.flags.join(", ") },
  ];

  const invalidatePreview = (): void => {
    setPreviewValues(null); setOutcome(null); form.clearErrors();
    // Resetting a pending mutation detaches its observer without cancelling the
    // import. Keep its native busy state across dialog close/reopen.
    if (!importState.fetching) importState.reset();
    clearSelectedIds(); setPage(1);
  };

  const loadPreview = (values: PreviewValues): void => {
    setOutcome(null);
    importState.reset();
    clearSelectedIds(); setPage(1); setPreviewValues(values);
  };

  const importSelected = async (selectedIds: ReadonlySet<string>, clear: () => void): Promise<void> => {
    if (!preview || !previewValues) return;
    try {
      const data = await runImport({ id: recordId, mailbox: previewValues.mailbox.trim(), uidvalidity: preview.uidvalidity, uids: [...selectedIds].map(Number) });
      if (data?.import_imap_sample) { setOutcome(data.import_imap_sample); clear(); }
    } catch { /* Authored mutation state renders the server error. */ }
  };

  return <DialogForm
    open={open}
    onOpenChange={(nextOpen) => { setOpen(nextOpen); if (!nextOpen) invalidatePreview(); }}
    title={t("channel.imap.sample.title")}
    description={t("channel.imap.sample.description")}
    size="lg"
    onSubmit={form.handleSubmit(loadPreview)}
    trigger={
      <RecordActionTrigger disabled={record.lifecycle !== "PAUSED"}>
        {t("channel.imap.sample.button")}
      </RecordActionTrigger>
    }
    footer={<Button type="submit" variant="primary" disabled={busy}>{previewFeed.isFetching ? t("channel.imap.sample.previewing") : t("channel.imap.sample.preview")}</Button>}>
      <FieldRow label={t("channel.imap.sample.mailbox")}><Input disabled={busy} invalid={Boolean(form.formState.errors.mailbox)} {...form.register("mailbox", {
        validate: (value) => Boolean(value.trim()) || t("channel.imap.sample.invalidRange"), onChange: invalidatePreview,
      })} /></FieldRow>
      <Controller name="allDates" control={form.control} render={({ field }) => <FieldRoot>
        <FieldRoot.Label>{t("channel.imap.sample.allDates")}</FieldRoot.Label>
        <Checkbox ref={field.ref} name={field.name} checked={field.value} disabled={busy} onBlur={field.onBlur}
          onCheckedChange={(checked) => { field.onChange(checked); invalidatePreview(); }} />
      </FieldRoot>} />
      <FieldRow label={t("channel.imap.sample.since")}><Input type="date" disabled={busy || allDates} {...form.register("since", { onChange: invalidatePreview })} /></FieldRow>
      <FieldRow label={t("channel.imap.sample.before")}><Input type="date" disabled={busy || allDates} invalid={Boolean(form.formState.errors.before)} {...form.register("before", {
        validate: (value, values) => {
          if (values.allDates) return true;
          const span = Date.parse(`${value}T00:00:00Z`) - Date.parse(`${values.since}T00:00:00Z`);
          if (!Number.isFinite(span) || span <= 0) return t("channel.imap.sample.invalidRange");
          return span / 86_400_000 <= 366 || t("channel.imap.sample.rangeTooLong");
        }, onChange: invalidatePreview,
      })} /></FieldRow>
      <FieldRow label={t("channel.imap.sample.limit")}><Input type="number" min={1} max={IMAP_SAMPLE_LIMIT} disabled={busy} invalid={Boolean(form.formState.errors.limit)} {...form.register("limit", {
        valueAsNumber: true, required: t("channel.imap.sample.invalidLimit", { limit: IMAP_SAMPLE_LIMIT }),
        min: { value: 1, message: t("channel.imap.sample.invalidLimit", { limit: IMAP_SAMPLE_LIMIT }) },
        max: { value: IMAP_SAMPLE_LIMIT, message: t("channel.imap.sample.invalidLimit", { limit: IMAP_SAMPLE_LIMIT }) },
        validate: (value) => Number.isInteger(value) || t("channel.imap.sample.invalidLimit", { limit: IMAP_SAMPLE_LIMIT }),
        onChange: invalidatePreview,
      })} /></FieldRow>
      {formError || operationError ? <Alert className="col-span-full" tone="danger">{formError ?? errorMessage(operationError, t("channel.imap.sample.failed"))}</Alert> : null}
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
          loaded: rows.length, total: page?.count,
          uidvalidity: preview.uidvalidity, upperUid: preview.upperUid,
        })}</Alert>
        <ControlBandProvider host={undefined}>
          <RowsListView scope="inherit" rows={rows} columns={columns} selectable emptyContent={t("channel.imap.sample.empty")}
            bulkActions={(selectedIds, clear) => <Button size="sm" variant="primary" disabled={busy || selectedIds.size > IMAP_SAMPLE_LIMIT}
              title={selectedIds.size > IMAP_SAMPLE_LIMIT ? t("channel.imap.sample.selectionTooLarge", { limit: IMAP_SAMPLE_LIMIT }) : undefined}
              onClick={() => void importSelected(selectedIds, clear)}>{importState.fetching ? t("channel.imap.sample.importing") : t("channel.imap.sample.importSelected", { count: selectedIds.size })}</Button>} />
        </ControlBandProvider>
        {previewFeed.hasNextPage ? <Button type="button" variant="secondary" disabled={busy}
          onClick={() => void previewFeed.fetchNextPage()}>{previewFeed.isFetching ? t("channel.imap.sample.previewing") : t("channel.imap.sample.loadOlder")}</Button> : null}
      </div> : null}
    </DialogForm>;
}
