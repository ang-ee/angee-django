import { ResourceQuery, useModelMetadata } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import {
  Button, ErrorBanner, InlineEmpty, PaneLabelPopover, PaneRow, PaneRows, RecordReferenceRow, RelationPicker, Skeleton, SkeletonStatus, SkeletonText,
  errorMessage, useAuthoredResourceMutation, useRelatedGroups, useRouteHref, useRuntimeViewAs, type ChatterViewContext, type ContainerChild, type RelatedGroupContent,
  type RelatedGroupLabel,
} from "@angee/ui";
import { useMemo, useState, type ReactElement } from "react";

import {
  KnowledgeBindRecord, KnowledgePage, KnowledgePageRecordBindings, KnowledgePages, KnowledgeRecordPages,
  KnowledgeUnbindRecord, PAGE_MODEL, PAGE_READ_MODELS, RECORD_BINDING_MODEL,
} from "./data/documents";
import { useKnowledgeT } from "./i18n";

const BINDING_MODELS = [RECORD_BINDING_MODEL] as const;

export interface RecordPagesTarget { modelLabel: string; recordId: string }

/** Shared target identity for bound page groups and dated note streams. */
export function recordPagesTarget(context: ChatterViewContext): RecordPagesTarget {
  return { modelLabel: context.route?.modelLabel ?? "", recordId: context.view.kind === "record" ? context.view.sqid ?? "" : "" };
}

/** Knowledge owns role claims; the framework owns edge eligibility. */
interface RecordPagesGroupContent extends RelatedGroupContent {
  bindingRole?: string;
}

/** All groups observe one cached read per record, then partition only on the client. */
function useRecordPagesRead(context: ChatterViewContext, role?: string) {
  const preview = useRuntimeViewAs();
  const { modelLabel, recordId } = recordPagesTarget(context);
  const groups = useRelatedGroups<RecordPagesGroupContent>(context);
  const claimedRoles = new Set(groups.flatMap(({ content }) =>
    content.edge?.model === RECORD_BINDING_MODEL && content.bindingRole !== undefined ? [content.bindingRole] : []));
  const variables = useMemo(() => ({ modelLabel, recordId, role: null }), [modelLabel, recordId]);
  const query = useAuthoredQuery(KnowledgeRecordPages, variables, {
    enabled: Boolean(modelLabel && recordId), models: BINDING_MODELS,
  });
  const bindings = query.data?.record_knowledge_bindings.filter((binding) => binding.page !== null
    && (role === undefined ? !claimedRoles.has(binding.role) : binding.role === role));
  const canWrite = Boolean(query.data?.record_knowledge_can_bind && !preview.viewAs && !preview.pending);
  return { query, bindings, canWrite, canBind: canWrite && (role !== undefined || !claimedRoles.has("related")) };
}

export interface RecordPagesGroupOptions {
  label?: string;
  role?: string;
  sequence?: number;
  when?: RelatedGroupContent["when"];
}

/** A role-scoped Pages group, for related#groups or a model's groups address. */
export function recordPagesGroup(options: RecordPagesGroupOptions = {}): ContainerChild<RecordPagesGroupContent> {
  const { label = "Pages", role, sequence = 20, when } = options;
  return {
    sequence,
    content: {
      label,
      edge: { model: RECORD_BINDING_MODEL },
      bindingRole: role,
      when,
      useCount: (context) => useRecordPagesRead(context, role).bindings?.length,
      useVerb: (context) => useRecordPagesRead(context, role).canBind,
      render: (context, label) => <RecordPagesGroup key={context.view.sqid} context={context} role={role} label={label} />,
    },
  };
}

/** The picker and rows share mutation state without a second binding or provider. */
function RecordPagesGroup({ context, role, label }: { context: ChatterViewContext; role?: string; label: RelatedGroupLabel }): ReactElement {
  const t = useKnowledgeT();
  const routeHref = useRouteHref();
  const { query, bindings, canWrite, canBind } = useRecordPagesRead(context, role);
  const [open, setOpen] = useState(false);
  const [pageToBind, setPageToBind] = useState("");
  const [search, setSearch] = useState("");
  const metadata = useModelMetadata(PAGE_MODEL);
  const where = useMemo(() => metadata ? ResourceQuery.from(metadata).toWhere({
    is_trashed: { exact: false }, writable: { exact: true }, title: { iContains: search },
    id: { notInList: (bindings ?? []).filter((binding) => binding.role === (role ?? "related"))
      .flatMap((binding) => binding.page ? [binding.page] : []) },
  }) : undefined, [bindings, metadata, role, search]);
  const pagesQuery = useAuthoredQuery(KnowledgePages, { limit: 20, offset: 0, where, order: [{ title: "asc" }] }, {
    enabled: canBind && open && Boolean(metadata), models: [PAGE_MODEL],
  });
  const [bind, bindState] = useAuthoredResourceMutation(KnowledgeBindRecord, { invalidateModels: BINDING_MODELS });
  const [unbind, unbindState] = useAuthoredResourceMutation(KnowledgeUnbindRecord, { invalidateModels: BINDING_MODELS });
  const busy = bindState.fetching || unbindState.fetching;

  async function changeBinding(page: string, remove: boolean, bindingRole = role ?? "related"): Promise<void> {
    if (!(remove ? canWrite : canBind) || busy) return;
    const { modelLabel, recordId } = recordPagesTarget(context);
    const input = { model_label: modelLabel, record_id: recordId, page, role: bindingRole };
    try {
      if (remove) await unbind({ input });
      else { await bind({ input }); setOpen(false); setPageToBind(""); }
    } catch { /* The mutation owner exposes the error below. */ }
  }

  return <>
    {label(<PaneLabelPopover icon="plus" label={t("record.bind")} open={open} onOpenChange={setOpen}>
      {bindState.error ? <ErrorBanner description={errorMessage(bindState.error, t("record.writeError"))} /> : null}
      <RelationPicker aria-label={t("record.choosePage")} value={pageToBind} onChange={setPageToBind}
        placeholder={t("record.choosePage")} onSearchChange={setSearch}
        options={(pagesQuery.data?.pages ?? []).map((page) => ({ value: page.id, label: page.title,
          description: page.vault_label ?? undefined }))}
        searchState={{ pending: pagesQuery.isFetching, error: pagesQuery.error?.message, retry: () => void pagesQuery.refetch() }} />
      <Button type="button" size="sm" variant="secondary" disabled={!pageToBind || busy || !canBind}
        onClick={() => void changeBinding(pageToBind, false)}>{t("record.bind")}</Button>
    </PaneLabelPopover>)}
    <li>
      {query.isPending && !query.data ? <SkeletonStatus label={t("record.loading")} className="p-2">
        <SkeletonText lines={2} size="lg" />
      </SkeletonStatus> : query.error && !query.data ? <ErrorBanner description={errorMessage(query.error, t("record.loadError"))} /> :
        <div className="flex min-h-0 flex-col">
          {unbindState.error ? <ErrorBanner description={errorMessage(unbindState.error, t("record.writeError"))} /> : null}
          {bindings?.length ? <PaneRows>
            {bindings.map((binding) => <li key={binding.id}>
              {/* Unbind is the row's verb; the binding role is a declared id, never shown raw. */}
              <PaneRow icon="note" title={binding.page_title || t("record.untitledPage")}
                href={routeHref("knowledge.page", { id: binding.page ?? "" })}
                verb={canWrite && binding.page_can_write ? <Button type="button" size="sm" variant="ghost"
                  disabled={busy} aria-label={`${t("record.unbind")} ${binding.page_title || t("record.untitledPage")}`}
                  onClick={() => binding.page && void changeBinding(binding.page, true, binding.role)}>{t("record.unbind")}</Button> : undefined} />
            </li>)}
          </PaneRows> : <InlineEmpty icon="knowledge" label={t("record.empty")} />}
        </div>}
    </li>
  </>;
}

/** The pages that link to the open page: a `knowledge.Page#groups` child. */
export const pageBacklinksGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 10,
  content: {
    label: "Backlinks",
    useCount: (context) => usePageDetail(context).data?.pages_by_pk?.backlinks.length,
    render: (context, label) => <>{label()}<li><PageBacklinks context={context} /></li></>,
  },
};

/** The records the open page is bound to: a `knowledge.Page#groups` child. */
export const pageBoundToGroup: ContainerChild<RelatedGroupContent> = {
  sequence: 20,
  content: {
    label: "Bound to",
    useCount: (context) => usePageRecordBindings(context).data?.page_record_bindings.length,
    render: (context, label) => <>{label()}<li><PageBoundTo context={context} /></li></>,
  },
};

function pageId(context: ChatterViewContext): string {
  return context.view.kind === "record" ? context.view.sqid ?? "" : "";
}

function usePageDetail(context: ChatterViewContext) {
  const id = pageId(context);
  return useAuthoredQuery(KnowledgePage, { id }, { enabled: Boolean(id), models: PAGE_READ_MODELS });
}

function usePageRecordBindings(context: ChatterViewContext) {
  const page = pageId(context);
  return useAuthoredQuery(KnowledgePageRecordBindings, { page }, { enabled: Boolean(page), models: BINDING_MODELS });
}

function PageBacklinks({ context }: { context: ChatterViewContext }): ReactElement {
  const t = useKnowledgeT();
  const routeHref = useRouteHref();
  const query = usePageDetail(context);
  const backlinks = query.data?.pages_by_pk?.backlinks ?? [];
  if (query.isPending && !query.data) return <SkeletonStatus label={t("page.loading")}><Skeleton size="lg" /></SkeletonStatus>;
  if (query.error) return <ErrorBanner description={errorMessage(query.error, t("page.loadError"))} />;
  return <PaneRows>
    {backlinks.map((backlink) => {
      const href = routeHref("knowledge.page", { id: backlink.page });
      return <li key={`${backlink.page}:${backlink.display_text}`}>
        <PaneRow icon="note" title={backlink.title} sub={backlink.display_text || undefined} href={href} />
      </li>;
    })}
  </PaneRows>;
}

function PageBoundTo({ context }: { context: ChatterViewContext }): ReactElement {
  const t = useKnowledgeT();
  const query = usePageRecordBindings(context);
  const bindings = query.data?.page_record_bindings.map((binding) => ({
    key: binding.id, model: binding.model_label, id: binding.record_id, role: binding.role,
  })) ?? [];
  if (query.isPending && !query.data) return <SkeletonStatus label={t("page.loading")}><Skeleton size="lg" /></SkeletonStatus>;
  if (query.error) return <ErrorBanner description={errorMessage(query.error, t("page.loadError"))} />;
  return <PaneRows>
    {bindings.map((binding) => <li key={binding.key}>
      <RecordReferenceRow model={binding.model} id={binding.id} references={bindings} />
    </li>)}
  </PaneRows>;
}
