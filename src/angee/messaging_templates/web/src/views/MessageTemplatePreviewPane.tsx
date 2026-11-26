import * as React from "react";
import { useDebounce } from "use-debounce";
import type { DocumentType } from "@angee/gql/console";
import type { MessageTemplateTextDirection, Message_Templates_Set_Input } from "@angee/gql/console/graphql";
import { useAuthoredQuery } from "@angee/refine";
import {
  EmptyState,
  ErrorBanner,
  LoadingPanel,
  MessageHtmlFrame,
  MetaGrid,
  PaneToolbar,
  RecordIssues,
  SegmentedControl,
  Select,
  cn,
  errorMessage,
  textRoleVariants,
  useFormViewValues,
  useRelationOptions,
  type RecordIssue,
  type RecordPanelContext,
} from "@angee/ui";

import { MessageTemplatePreview } from "../documents.console";
import { useMessagingTemplatesT } from "../i18n";
import { BRAND_KIT_MODEL, BRAND_KIT_RELATION, MESSAGE_TEMPLATE_MODEL } from "../resources";

/** A row's preview reads again when a row or a kit changes. */
const PREVIEW_MODELS = [MESSAGE_TEMPLATE_MODEL, BRAND_KIT_MODEL];
/** What the kit select reads beside each kit's id and label. */
const KIT_FIELDS = ["is_default"];

/**
 * How long the open form's edits stay unchanged before the pane reads their preview. A preview of edits runs the
 * save's validation and a walk of the row, so it is read once an edit settles, never once per keystroke.
 */
export const PREVIEW_PAUSE_MS = 400;

type PreviewFormat = "html" | "text";

/** The HTML `dir` of the direction the server rendered the mail in, which the mail's own HTML says. */
const TEXT_DIRECTION: Readonly<Record<MessageTemplateTextDirection, "rtl" | "ltr">> = { RTL: "rtl", LTR: "ltr" };

export interface MessageTemplatePreviewPaneProps {
  /** The saved row's public id. */
  template: string;
  /** The pane is the selected one: an unselected pane reads nothing. */
  active: boolean;
  /** The row's kind carries a subject and a preheader, as the form decides for its Message section. */
  carriesMessageFields: boolean;
  /** The record form's save surface: the pane previews its unsaved edits as its Save would write them. */
  form: RecordPanelContext["form"];
  /** Bring the person to a field of the record form, as a refusal naming it offers. */
  focusField: RecordPanelContext["focusField"];
}

/**
 * What the open form's Save would send now (`nextSave`), as text, the form's to compare by value since each read is
 * a new object; null when it would send no edit: the form holds none, takes none (it is read-only, or stopped taking
 * edits, as when a permission is withdrawn while it is dirty), or holds only edits its Save cannot write. Read it in
 * render, never in a form subscription's callback.
 */
export function unsavedEdits(form: Pick<RecordPanelContext["form"], "formIsDirty" | "formTakesEdits" | "nextSave">):
  string | null {
  const data = form.formIsDirty && form.formTakesEdits ? form.nextSave().data : {};
  return Object.keys(data).length ? JSON.stringify(data) : null;
}

type PreviewWarning = DocumentType<typeof MessageTemplatePreview>["message_template_preview"]["warnings"][number];

/**
 * What a preview says of the render, one issue per warning, in the server's words and under the column it names, if
 * any: a rendered row's warnings, or the reasons nothing renders. The same words can come under two columns ("Give
 * an object." under variables and under sample), so each issue is told apart by its column too.
 */
export function previewIssues(warnings: readonly PreviewWarning[], rendered: boolean): RecordIssue[] {
  return warnings.map((warning) => ({
    id: `${warning.code}:${warning.field ?? ""}:${warning.detail}`,
    tone: rendered ? "warning" as const : "danger" as const,
    message: warning.detail,
    ...(warning.field ? { field: warning.field } : {}),
  }));
}

/**
 * The open form's unsaved edits (`unsavedEdits`) once they have stayed the same for `PREVIEW_PAUSE_MS`, or null while
 * there are none. That null is known at once, with no pause, so Discard and Save return the pane to the saved row
 * straight away. `settling` says newer edits are not yet the ones previewed.
 */
function useSettledPatch(form: RecordPanelContext["form"]): { patch: string | null; settling: boolean } {
  useFormViewValues(form); // render again on each edit
  const edits = unsavedEdits(form);
  const [settled, pause] = useDebounce(edits, PREVIEW_PAUSE_MS);
  React.useEffect(() => {
    if (edits === null) pause.flush();
  }, [edits, pause]);
  const patch = edits === null ? null : settled;
  return { patch, settling: edits !== patch };
}

/**
 * The row as a recipient receives it: rendered by the server with its sample, in
 * the brand kit chosen here or, unset, the deployment's default kit; for a mail its
 * subject and preheader; its HTML body or its plain-text body; and what the server
 * warns about. While the record form holds unsaved edits its Save would write, the
 * pane renders those edits as Save would write them, the sample values among them,
 * and its caption says so; it reads them once they settle, and shows the saved row
 * again as soon as the form holds none. Edits Save would refuse are titled so,
 * each refusal in Save's words; edits Save would keep that the sample cannot render
 * say that instead; a saved row that does not render says so. A refusal that names
 * a field takes the person to it when they choose it, never by itself, since they
 * are editing elsewhere; each one is announced by the shared alert it is shown in.
 * The HTML is only ever in the framework's sandboxed message frame, which runs no
 * script, has no same origin and loads remote images only when asked; the frame's
 * own body styles sit under the mail, so a mail client may space and size it
 * slightly differently, which the pane says beside a frame. While newer edits
 * settle, or a preview for another kit or other edits loads, the earlier one stays
 * dimmed and inert under the caption for what was asked for; while the shown one is
 * read again, as after a save, it stays as it is. The pane says it is busy throughout.
 *
 * The pane fills the height its record tab is given. The brand kit select and the
 * HTML or plain-text switch are its pane toolbar's (`PaneToolbar`, in the host the
 * tab renders it in). Under it, the line saying what is previewed and what the
 * server says of the render take at most part of the height and scroll within it;
 * the body takes the rest, and the frame or the plain text scrolls the whole mail
 * within it. Each region that scrolls can be reached and scrolled from the keyboard.
 * The warnings lead that band, so they are seen whole before anything else in it.
 * The subject, the preheader and the plain text read in the direction the server
 * rendered the mail in, as its frame does, whatever their first letter.
 */
export function MessageTemplatePreviewPane({
  template, active, carriesMessageFields, form, focusField,
}: MessageTemplatePreviewPaneProps): React.ReactElement {
  const t = useMessagingTemplatesT();
  const [kit, setKit] = React.useState("");
  const [format, setFormat] = React.useState<PreviewFormat>("html");
  // The kit resource's own rows in its own order; choosing the default kit by name reads what unset reads.
  const kits = useRelationOptions(BRAND_KIT_RELATION, { enabled: active, fields: KIT_FIELDS });
  const { patch, settling } = useSettledPatch(form);
  // Unset sends no kit, and the server renders in the default one; no edit sends no patch, the saved row's preview.
  const variables = React.useMemo(() => {
    // The edits as their Save would send them, which `nextSave` reads untyped: stated here, not checked.
    const patchInput: Message_Templates_Set_Input | undefined = patch === null ? undefined : JSON.parse(patch);
    return {
      template,
      ...(kit ? { brand_kit: kit } : {}),
      ...(patchInput === undefined ? {} : { patch: patchInput }),
    };
  }, [kit, patch, template]);
  const preview = useAuthoredQuery(MessageTemplatePreview, variables, {
    models: PREVIEW_MODELS,
    keepPreviousData: true,
    enabled: active,
    errorNotification: false,
  });
  const kitOptions = React.useMemo(() => {
    const defaults = new Set(kits.rows.filter((row) => row.is_default === true).map((row) => String(row.id)));
    return [
      { value: "", label: t("preview.kitDefault") },
      ...kits.options
        .filter((option) => !defaults.has(option.value))
        .map((option) => ({ value: option.value, label: option.label })),
    ];
  }, [kits.options, kits.rows, t]);
  const formatOptions = React.useMemo(() => [
    { value: "html" as const, label: t("preview.html") },
    { value: "text" as const, label: t("preview.text") },
  ], [t]);
  const result = preview.data?.message_template_preview;
  const rendered = result?.rendered ?? null;
  const issues = previewIssues(result?.warnings ?? [], rendered !== null);
  // Save would refuse the edits; else, with edits, Save would keep them but the sample does not render them.
  const saveRefused = result?.warnings.some((warning) => warning.code === "SAVE_REFUSED") ?? false;
  // What is on screen stands in for a preview not yet read (newer edits settle, or a new read shows the last
  // answer meanwhile): dimmed and inert as a list keeping its previous rows is, beside saying it is busy.
  const standIn = cn((settling || preview.isPlaceholderData) && "pointer-events-none opacity-50");
  const framed = rendered !== null && format === "html";
  const dir = rendered ? TEXT_DIRECTION[rendered.direction] : undefined;

  const toolbar = (
    <PaneToolbar
      selector={<Select aria-label={t("preview.kit")} size="sm" className="w-auto min-w-36" value={kit}
        onValueChange={setKit} options={kitOptions} loading={kits.list.fetching && kits.rows.length === 0} />}
      actions={rendered ? <SegmentedControl aria-label={t("preview.format")} options={formatOptions} value={format}
        onValueChange={setFormat} /> : null}
    />
  );

  // What the server warns about first, then whether this is the saved row or the unsaved edits and the rest of what
  // it says of the render, above the body: never more than part of the pane, scrolling within it.
  const facts = [
    preview.error ? <ErrorBanner key="error" description={errorMessage(preview.error, t("preview.loadError"))} /> : null,
    rendered ? <RecordIssues key="warnings" items={issues} onFocusField={focusField} /> : null,
    <p key="caption" className={textRoleVariants({ role: "meta" })}>
      {patch === null ? t("preview.saved") : t("preview.unsaved")}
    </p>,
    rendered && carriesMessageFields ? (
      <MetaGrid key="message" rows={[
        {
          id: "subject",
          label: t("preview.subject"),
          value: <span lang={rendered.language} dir={dir}>{rendered.subject}</span>,
        },
        {
          id: "preheader",
          label: t("preview.preheader"),
          value: <span lang={rendered.language} dir={dir}>{rendered.preheader}</span>,
        },
      ]} />
    ) : null,
    framed ? <p key="fidelity" className={textRoleVariants({ role: "caption" })}>{t("preview.fidelity")}</p> : null,
  ].filter((node) => node !== null);

  let body: React.ReactNode;
  if (!result) {
    body = preview.error ? null : <LoadingPanel density="inline" />;
  } else if (!rendered) {
    // The state first and the server's reasons under it, both at the top of what the pane is given.
    body = (
      <div tabIndex={0} role="region"
        aria-label={saveRefused ? t("preview.unsavedRefusedRegion") : t("preview.refusedRegion")}
        className={cn("grid min-h-0 flex-1 content-start gap-3 overflow-y-auto p-4", standIn)}>
        <EmptyState icon="triangle-alert" className="min-h-0 p-6" title={saveRefused ? t("preview.unsavedRefused")
          : patch === null ? t("preview.refused") : t("preview.unsavedUnrendered")} />
        <RecordIssues items={issues} onFocusField={focusField} />
      </div>
    );
  } else if (format === "text") {
    body = (
      <div lang={rendered.language} dir={dir} tabIndex={0} role="region" aria-label={t("preview.text")}
        className={cn("min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 text-sm text-fg", standIn)}>
        {rendered.plain_text}
      </div>
    );
  } else {
    body = (
      <MessageHtmlFrame html={rendered.html} title={t("preview.frameTitle")} lang={rendered.language}
        className={cn("h-auto min-h-0 flex-1 rounded-none border-0", standIn)} />
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-sheet"
      aria-busy={settling || preview.isFetching || preview.isPlaceholderData || undefined}>
      {toolbar}
      {result || preview.error ? (
        <div tabIndex={0} role="region" aria-label={t("preview.factsRegion")}
          className={cn("grid max-h-[40%] flex-none content-start gap-3 overflow-y-auto border-b border-border-subtle px-4 py-2",
            standIn)}>
          {facts}
        </div>
      ) : null}
      {body}
    </div>
  );
}
