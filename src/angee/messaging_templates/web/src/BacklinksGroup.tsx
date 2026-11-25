import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  ErrorBanner,
  PaneRow,
  PaneRows,
  PaneRowsSkeleton,
  errorMessage,
  optionLabel,
  useEnumOptions,
  useResourceRecordHref,
  useUiT,
  type ChatterViewContext,
  type ContainerChild,
  type RelatedGroupContent,
} from "@angee/ui";

import { MessageTemplateBacklinks } from "./documents.console";
import { useMessagingTemplatesT } from "./i18n";
import { MESSAGE_TEMPLATE_MODEL } from "./resources";

/** A change to any message template may change which rows name this one. */
const BACKLINK_MODELS = [MESSAGE_TEMPLATE_MODEL];

function recordId(context: ChatterViewContext): string {
  return context.view.kind === "record" ? context.view.sqid ?? "" : "";
}

/** The one read the group's count and its rows share. */
function useBacklinks(context: ChatterViewContext) {
  const id = recordId(context);
  return useAuthoredQuery(MessageTemplateBacklinks, { id }, { enabled: Boolean(id), models: BACKLINK_MODELS });
}

function BacklinksLabel(): React.ReactElement {
  const t = useMessagingTemplatesT();
  return <>{t("backlinks.label")}</>;
}

/**
 * The rows, in any language, that name the open row's kind and key: a layout's
 * templates, and every row that includes a block or part. Each opens its record;
 * a row that does not render, an overridden shipped default, says so.
 */
function MessageTemplateBacklinkRows({ context }: { context: ChatterViewContext }): React.ReactElement {
  const t = useMessagingTemplatesT();
  const ui = useUiT();
  const query = useBacklinks(context);
  const href = useResourceRecordHref(MESSAGE_TEMPLATE_MODEL);
  const kinds = useEnumOptions(MESSAGE_TEMPLATE_MODEL, "kind");
  if (query.isPending && !query.data) return <PaneRowsSkeleton loadingLabel={ui("loading.default")} rowCount={2} />;
  if (query.error && !query.data) {
    return <ErrorBanner description={errorMessage(query.error, t("backlinks.loadError"))} />;
  }
  return (
    <PaneRows>
      {(query.data?.message_templates_by_pk?.backlinks ?? []).map((row) => (
        <li key={row.id}>
          <PaneRow icon="message-templates" title={row.display_name} detail={row.language}
            sub={<>{optionLabel(kinds, row.kind)} · {row.key}</>}
            meta={row.is_effective ? undefined : t("backlinks.overridden")} href={href?.(row.id)} />
        </li>
      ))}
    </PaneRows>
  );
}

/**
 * The Backlinks group of a message template's Links tab. It counts the rows it
 * lists, so it is absent where nothing names the row, and for a reader who may
 * not preview the row, whose read answers no rows at all.
 */
export const messageTemplateBacklinksGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 10,
  content: {
    label: <BacklinksLabel />,
    usePaneCount: (context) => {
      const query = useBacklinks(context);
      return query.data ? query.data.message_templates_by_pk?.backlinks?.length ?? 0 : undefined;
    },
    render: (context, label) => <>{label()}<li><MessageTemplateBacklinkRows context={context} /></li></>,
  },
};
