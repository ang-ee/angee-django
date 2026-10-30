import { useModelMetadata } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import {
  Action, Button, EmptyState, ErrorBanner, Field, Form, RecordPager, Skeleton, SkeletonStatus, SkeletonText,
  WikilinkProvider, errorMessage, useRouteHref, useTrashActions, useUiT, type RecordNavigation,
} from "@angee/ui";
import { KnowledgePage, PAGE_MODEL, PAGE_READ_MODELS } from "./data/documents";
import { usePageSubmit } from "./data/use-page-submit";
import { useKnowledgeT } from "./i18n";
import { PageChildren } from "./views/PageChildren";

export interface KnowledgePageViewProps {
  pageId: string;
  navigation?: RecordNavigation | null;
}

/** A Knowledge page declares fields; Form owns its toolbar and edit lifecycle. */
export function KnowledgePageView({ pageId, navigation }: KnowledgePageViewProps) {
  const t = useKnowledgeT();
  const uiT = useUiT();
  const href = useRouteHref();
  const query = useAuthoredQuery(KnowledgePage, { id: pageId }, { models: PAGE_READ_MODELS });
  const resource = useModelMetadata(PAGE_MODEL)?.resource;
  const actions = useTrashActions(resource);
  const submit = usePageSubmit();
  const readError = query.error ? <ErrorBanner
    description={errorMessage(query.error, t("page.loadError"))}
    actions={<Button variant="secondary" size="sm" disabled={query.isFetching}
      onClick={() => { void query.refetch(); }}>{uiT("app.retry")}</Button>}
  /> : null;
  const detail = query.data?.pages_by_pk;
  if (!detail) {
    if (query.isPending) return <SkeletonStatus label={t("page.loading")} className="space-y-3 p-4">
      <Skeleton size="lg" className="w-2/3" /><SkeletonText lines={2} />
    </SkeletonStatus>;
    if (readError) return readError;
    return <EmptyState fill icon="note" title={t("page.notFoundTitle")} description={t("page.notFoundDescription")} />;
  }
  return <WikilinkProvider resolve={(title) => {
    const pageTitle = title.split("#", 1)[0]!.trim();
    const target = detail.wikilinks.find((link) => link.title === pageTitle)?.page;
    return target ? { broken: false, href: href("knowledge.page", { id: target }) } : { broken: true };
  }}>
    {readError}
    <Form key={pageId} resource={PAGE_MODEL} id={pageId} headerDensity="compact"
      acknowledgedSource={{ record: detail,
        values: { title: detail.title, body: detail.markdown?.body ?? "", is_trashed: detail.is_trashed },
        reload: async () => (await query.refetch()).data?.pages_by_pk ?? null }}
      submit={detail.markdown ? submit : undefined}
      eyebrow={[detail.vault_label, ...detail.ancestors.map((page) => page.title)].filter(Boolean).join(" / ")}
      toolbar={navigation ? <RecordPager navigation={navigation} /> : undefined}
      recordExtras={() => !detail.is_trashed && (detail.kind === "folder" || detail.has_children)
        ? <PageChildren page={detail.id} vault={detail.vault} /> : null}>
      <Field name="title" title label={t("editor.titleLabel")} />
      <Field name="is_trashed" hidden readOnly />
      {detail.markdown ? <Field name="body" body="document" widget="markdown.editor" /> : null}
      {actions.map((action) => <Action key={action.id} {...action} />)}
    </Form>
  </WikilinkProvider>;
}
