import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  EmptyState,
  ErrorBanner,
  LoadingPanel,
  MessageHtmlFrame,
  MetaGrid,
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
}

/**
 * The saved row as a recipient receives it: rendered by the server with its
 * sample, in the brand kit chosen here or, unset, the deployment's default kit;
 * its subject and preheader, its HTML body or its plain-text body, and what the
 * server warns about. A row that does not render shows each reason as the server
 * words it. The HTML is only ever in the framework's sandboxed message frame,
 * which runs no script, has no same origin and loads remote images only when
 * asked; the frame's own body styles sit under the mail, so a mail client may
 * space and size it slightly differently. While another kit's preview loads, the
 * previous one stays on screen and the pane says it is busy.
 */
export function MessageTemplatePreviewPane({ template, active }: MessageTemplatePreviewPaneProps): React.ReactElement {
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

  let body: React.ReactNode;
  if (!result) {
    body = preview.error ? null : <LoadingPanel density="inline" />;
  } else if (!rendered) {
    body = <EmptyState icon="triangle-alert" title={t("preview.refused")} fill />;
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
        className="h-auto min-h-96 flex-1 rounded-none border-0" />
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-sheet" aria-busy={preview.isPlaceholderData || undefined}>
      <div className="grid gap-3 border-b border-border-subtle px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <p className={textRoleVariants({ role: "meta", className: "min-w-0 flex-1" })}>{t("preview.saved")}</p>
          <Select aria-label={t("preview.kit")} size="sm" value={kit} onValueChange={setKit} options={kitOptions}
            loading={kits.list.fetching && kits.rows.length === 0} />
          {rendered ? (
            <SegmentedControl aria-label={t("preview.format")} options={formatOptions} value={format}
              onValueChange={setFormat} />
          ) : null}
        </div>
        {preview.error ? <ErrorBanner description={errorMessage(preview.error, t("preview.loadError"))} /> : null}
        {rendered ? (
          <MetaGrid rows={[
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
        ) : null}
        <RecordIssues items={issues} />
        <p className={textRoleVariants({ role: "caption" })}>{t("preview.fidelity")}</p>
      </div>
      {body}
    </div>
  );
}
