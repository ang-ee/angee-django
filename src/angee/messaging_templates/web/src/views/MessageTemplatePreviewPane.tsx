import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Alert,
  ErrorBanner,
  LoadingPanel,
  MessageHtmlFrame,
  MetaGrid,
  RecordIssues,
  SegmentedControl,
  Select,
  errorMessage,
  textRoleVariants,
  type RecordIssue,
} from "@angee/ui";

import { MessageTemplatePreview, MessageTemplatePreviewKits } from "../documents.console";
import { useMessagingTemplatesT } from "../i18n";
import { BRAND_KIT_MODEL, MESSAGE_TEMPLATE_MODEL } from "../resources";

/** A saved row's preview reads again when a row or a kit changes. */
const PREVIEW_MODELS = [MESSAGE_TEMPLATE_MODEL, BRAND_KIT_MODEL];
const KIT_MODELS = [BRAND_KIT_MODEL];

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
 * space and size it slightly differently.
 */
export function MessageTemplatePreviewPane({ template, active }: MessageTemplatePreviewPaneProps): React.ReactElement {
  const t = useMessagingTemplatesT();
  const [kit, setKit] = React.useState("");
  const [format, setFormat] = React.useState<PreviewFormat>("html");
  const kits = useAuthoredQuery(MessageTemplatePreviewKits, undefined, { models: KIT_MODELS, enabled: active });
  // Unset sends no kit, and the server renders in the default one.
  const variables = React.useMemo(() => (kit ? { template, brand_kit: kit } : { template }), [kit, template]);
  const preview = useAuthoredQuery(MessageTemplatePreview, variables, {
    models: PREVIEW_MODELS,
    keepPreviousData: true,
    enabled: active,
  });
  const kitOptions = React.useMemo(() => [
    { value: "", label: t("preview.kitDefault") },
    ...(kits.data?.brand_kits ?? [])
      .filter((option) => !option.is_default)
      .map((option) => ({ value: option.id, label: option.name })),
  ], [kits.data, t]);
  const formatOptions = React.useMemo(() => [
    { value: "html" as const, label: t("preview.html") },
    { value: "text" as const, label: t("preview.text") },
  ], [t]);
  const result = preview.data?.message_template_preview;
  const rendered = result?.rendered ?? null;
  const issues: RecordIssue[] = rendered
    ? (result?.warnings ?? []).map((warning, index) => ({
        id: `${warning.code}-${index}`,
        tone: "warning" as const,
        message: warning.detail,
      }))
    : [];

  let body: React.ReactNode;
  if (!result && preview.error) {
    body = <div className="p-4"><ErrorBanner description={errorMessage(preview.error, t("preview.loadError"))} /></div>;
  } else if (!result) {
    body = <LoadingPanel density="inline" />;
  } else if (!rendered) {
    body = (
      <div className="p-4">
        <Alert tone="danger" title={t("preview.refused")}>
          <ul className="grid gap-1">
            {result.warnings.map((warning, index) => <li key={index}>{warning.detail}</li>)}
          </ul>
        </Alert>
      </div>
    );
  } else if (format === "text") {
    body = (
      <div lang={rendered.language} dir="auto"
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
    <div className="flex min-h-0 flex-1 flex-col bg-sheet">
      <div className="grid gap-3 border-b border-border-subtle px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <p className={textRoleVariants({ role: "meta", className: "min-w-0 flex-1" })}>{t("preview.saved")}</p>
          <Select aria-label={t("preview.kit")} size="sm" value={kit} onValueChange={setKit} options={kitOptions}
            loading={kits.isPending} />
          {rendered ? (
            <SegmentedControl aria-label={t("preview.format")} options={formatOptions} value={format}
              onValueChange={setFormat} />
          ) : null}
        </div>
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
