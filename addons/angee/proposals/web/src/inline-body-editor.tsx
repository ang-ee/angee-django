import { Button, ErrorBanner, FormRoot, Textarea, errorMessage } from "@angee/ui";
import * as React from "react";

import { useProposalsT } from "./i18n";

/**
 * The multi-line in-place editor the comparison answers and the proposal statements share: a
 * textarea, an optional row of controls beside the verbs, Save and Cancel, and the failure
 * under it. The host owns the write; this owns the ceremony.
 */
export function InlineBodyEditor({ value, label, rows = 3, pending, submitLabel, cancel = true, extra, fallback, onSave, onCancel }: {
  value: string;
  /** The textarea's accessible name and placeholder. */
  label: string;
  rows?: number;
  pending: boolean;
  submitLabel: string;
  /** Whether the editor can be left without saving. */
  cancel?: boolean;
  /** Controls rendered before the verbs (an audience picker). */
  extra?: React.ReactNode;
  /** The failure message when the write throws without a message of its own. */
  fallback: string;
  onSave: (body: string) => Promise<boolean | void>;
  onCancel: () => void;
}): React.ReactElement {
  const t = useProposalsT();
  const [body, setBody] = React.useState(value);
  const [failure, setFailure] = React.useState<string | null>(null);
  return <FormRoot className="space-y-2" onSubmit={(event) => {
    event.preventDefault();
    if (!body.trim() || pending) return;
    void onSave(body.trim())
      .then((saved) => { if (saved !== false) setFailure(null); })
      .catch((error) => setFailure(errorMessage(error, fallback)));
  }}>
    <Textarea value={body} onChange={(event) => setBody(event.target.value)} aria-label={label} placeholder={label} rows={rows} />
    <div className="flex flex-wrap items-center gap-2">
      {extra}
      <Button type="submit" variant="primary" size="sm" loading={pending} disabled={!body.trim() || pending}>{submitLabel}</Button>
      {cancel ? <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={onCancel}>{t("comparison.cancel")}</Button> : null}
    </div>
    {failure ? <ErrorBanner description={failure} /> : null}
  </FormRoot>;
}
