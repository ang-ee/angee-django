import * as React from "react";
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
  errorMessage,
  textRoleVariants,
  useRelationOptions,
  type RecordIssue,
} from "@angee/ui";

import { MessageTemplatePreview } from "../documents.console";
import { useMessagingTemplatesT } from "../i18n";
import { BRAND_KIT_MODEL, BRAND_KIT_RELATION, MESSAGE_TEMPLATE_MODEL } from "../resources";

/** A saved row's preview reads again when a row or a kit changes. */
const PREVIEW_MODELS = [MESSAGE_TEMPLATE_MODEL, BRAND_KIT_MODEL];
/** What the kit select reads beside each kit's id and label. */
const KIT_FIELDS = ["is_default"];

type PreviewFormat = "html" | "text";

export interface MessageTemplatePreviewPaneProps {
  /** The saved row's public id. */
  template: string;
  /** The pane is the selected one: an unselected pane reads nothing. */
  active: boolean;
  /** The row's kind carries a subject and a preheader, as the form decides for its Message section. */
  carriesMessageFields: boolean;
}

/**
 * The saved row as a recipient receives it: rendered by the server with its
 * sample, in the brand kit chosen here or, unset, the deployment's default kit;
 * for a mail its subject and preheader; its HTML body or its plain-text body; and
 * what the server warns about. A row that does not render says so and shows each
 * reason as the server words it. The HTML is only ever in the framework's
 * sandboxed message frame, which runs no script, has no same origin and loads
 * remote images only when asked; the frame's own body styles sit under the mail,
 * so a mail client may space and size it slightly differently, which the pane
 * says beside a frame. While another kit's preview loads, the previous one stays
 * on screen and the pane says it is busy.
 *
 * The pane fills the height its record tab is given. The brand kit select and the
 * HTML or plain-text switch are its pane toolbar's (`PaneToolbar`, in the host the
 * tab renders it in); what the server says of the render takes at most part of
 * the height and scrolls within it; the body takes the rest, and the frame or the
 * plain text scrolls the whole mail within it.
 */
export function MessageTemplatePreviewPane({
  template, active, carriesMessageFields,
}: MessageTemplatePreviewPaneProps): React.ReactElement {
  const t = useMessagingTemplatesT();
  const [kit, setKit] = React.useState("");
  const [format, setFormat] = React.useState<PreviewFormat>("html");
  // The kit resource's own rows in its own order; choosing the default kit by name reads what unset reads.
  const kits = useRelationOptions(BRAND_KIT_RELATION, { enabled: active, fields: KIT_FIELDS });
  // Unset sends no kit, and the server renders in the default one.
  const variables = React.useMemo(() => (kit ? { template, brand_kit: kit } : { template }), [kit, template]);
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
  // A rendered row's warnings, or the reasons a row does not render, each as the server words it.
  const issues: RecordIssue[] = (result?.warnings ?? []).map((warning) => ({
    id: `${warning.code}:${warning.detail}`,
    tone: rendered ? "warning" as const : "danger" as const,
    message: warning.detail,
  }));
  const framed = rendered !== null && format === "html";

  const toolbar = (
    <PaneToolbar
      selector={<Select aria-label={t("preview.kit")} size="sm" className="w-auto min-w-36" value={kit}
        onValueChange={setKit} options={kitOptions} loading={kits.list.fetching && kits.rows.length === 0} />}
      actions={rendered ? <SegmentedControl aria-label={t("preview.format")} options={formatOptions} value={format}
        onValueChange={setFormat} /> : null}
    />
  );

  // What the server says of the render, above the body: never more than part of the pane, scrolling within it.
  const facts = [
    preview.error ? <ErrorBanner key="error" description={errorMessage(preview.error, t("preview.loadError"))} /> : null,
    rendered && carriesMessageFields ? (
      <MetaGrid key="message" rows={[
        {
          id: "subject",
          label: t("preview.subject"),
          value: <span lang={rendered.language} dir="auto">{rendered.subject}</span>,
        },
        {
          id: "preheader",
          label: t("preview.preheader"),
          value: <span lang={rendered.language} dir="auto">{rendered.preheader}</span>,
        },
      ]} />
    ) : null,
    rendered ? <RecordIssues key="warnings" items={issues} /> : null,
    framed ? <p key="fidelity" className={textRoleVariants({ role: "caption" })}>{t("preview.fidelity")}</p> : null,
  ].filter((node) => node !== null);

  let body: React.ReactNode;
  if (!result) {
    body = preview.error ? null : <LoadingPanel density="inline" />;
  } else if (!rendered) {
    // The state first and the server's reasons under it, both at the top of what the pane is given.
    body = (
      <div className="grid min-h-0 flex-1 content-start gap-3 overflow-y-auto p-4">
        <EmptyState icon="triangle-alert" title={t("preview.refused")} className="min-h-0 p-6" />
        <RecordIssues items={issues} />
      </div>
    );
  } else if (format === "text") {
    body = (
      <div lang={rendered.language} dir="auto" tabIndex={0} role="region" aria-label={t("preview.text")}
        className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-4 text-sm text-fg">
        {rendered.plain_text}
      </div>
    );
  } else {
    body = (
      <MessageHtmlFrame html={rendered.html} title={t("preview.frameTitle")} lang={rendered.language}
        className="h-auto min-h-0 flex-1 rounded-none border-0" />
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-sheet" aria-busy={preview.isPlaceholderData || undefined}>
      {toolbar}
      {facts.length ? (
        <div className="grid max-h-[40%] flex-none content-start gap-3 overflow-y-auto border-b border-border-subtle px-4 py-3">
          {facts}
        </div>
      ) : null}
      {body}
    </div>
  );
}
