import { useAuthoredQuery } from "@angee/refine";
import { senderDisplayName } from "@angee/parties";
import { Alert, Avatar, Checkbox, LoadingPanel, PageHeader, Pager, DebouncedSearchInput, SectionEyebrow, Select, Tag, TreeView, avatarInitials } from "@angee/ui";
import { NexusGraphParties } from "../documents";
import { InboxSenders } from "./documents";
import { useNexusT } from "../i18n";
import { INBOX_MODELS, PAGE_SIZE, useInboxFilter, type InboxNavigation, type InboxScope } from "./state";

export function InboxSendersPane({ scope, navigation }: { scope: InboxScope; navigation: InboxNavigation }) {
  const t = useNexusT();
  const view = useInboxFilter();
  const query = useAuthoredQuery(InboxSenders, { coverage: scope.coverage, text: view.filter.textTerm("text"),
    includeSent: view.filter.facetValues("sent").includes("yes"), sort: view.state.sorting?.[0]?.id ?? "recent",
    link: view.filter.facetValues("link")[0] ?? "", page: view.state.pagination.pageIndex + 1 }, { models: INBOX_MODELS, enabled: !view.state.queryError });
  const circles = useAuthoredQuery(NexusGraphParties, { limit: 200 }, { models: ["parties.Circle", "parties.Party"] });
  const error = view.state.queryError ?? query.error;
  return <section aria-label={t("inbox.senders")} className="flex h-full min-h-0 flex-col">
    <PageHeader density="compact" headingLevel={2} title={t("inbox.senders")} />
    <div className="space-y-3 p-3">
      <DebouncedSearchInput size="sm" surface="inset" aria-label={t("inbox.findSender")} placeholder={t("inbox.findSender")} value={view.filter.textTerm("text")} onValueChange={view.setText} />
      <TreeView rows={[{ id: "all", name: t("inbox.everyone"), icon: "comments" }]} icon="icon" selectedId={!scope.sender && !scope.circle ? "all" : undefined} onSelect={() => navigation.select()} />
      <div className="grid grid-cols-2 gap-2"><Select size="sm" aria-label={t("inbox.recent")} value={view.state.sorting?.[0]?.id ?? "recent"} onValueChange={id => view.setSorting([{ id, desc: true }])}
        options={["recent", "name", "count", "first"].map(value => ({ value, label: t(`inbox.${value}`) }))} />
        <Select size="sm" aria-label={t("inbox.linkState")} value={view.filter.facetValues("link")[0] ?? ""} onValueChange={value => view.setValue("link", value)} options={[{ value: "", label: t("inbox.all") }, ...["confirmed", "suggested", "unlinked"].map(value => ({ value, label: t(`inbox.${value}`) }))]} /></div>
    </div>
    <div className="min-h-0 flex-1 overflow-auto px-3">
      {error ? <Alert tone="danger">{error.message}</Alert> : query.isPending ? <LoadingPanel /> : <TreeView rows={query.data?.inbox_senders.rows ?? []} badge="count" selectedId={navigation.sender}
        onSelect={row => navigation.select(row.id)} emptyContent={t("inbox.noSenders")} renderRow={row => <span className="flex min-w-0 items-center gap-2">
          <Avatar size="sm" initials={avatarInitials(senderDisplayName(row.handle))} /><span className="min-w-0"><span className="block truncate">{senderDisplayName(row.handle)}</span><span className="block truncate text-2xs text-fg-muted" title={row.preview}>{row.preview || row.handle.value}</span></span>
          {!row.party && row.handle.party ? <Tag>{t("inbox.suggested")}</Tag> : null}
        </span>} />}
    </div>
    <div className="space-y-3 border-t border-border-subtle p-3">
      <div className="flex items-center gap-1"><Pager page={view.state.pagination.pageIndex + 1} pageSize={PAGE_SIZE} total={query.data?.inbox_senders.count} onPageChange={view.setPage} labelElement="span" disabled={query.isFetching} /></div>
      <label className="flex items-center gap-2 text-2xs text-fg-muted"><Checkbox size="sm" checked={view.filter.facetValues("sent").includes("yes")} onCheckedChange={value => view.setValue("sent", value ? "yes" : "")} />{t("inbox.sent")}</label>
      <SectionEyebrow>{t("inbox.circles")}</SectionEyebrow>
      <TreeView rows={circles.data?.circles ?? []} label="name" selectedId={navigation.circle} onSelect={row => navigation.select(undefined, row.id)} />
    </div>
  </section>;
}
