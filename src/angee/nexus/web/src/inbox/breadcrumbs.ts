import { useMemo } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { useBreadcrumbTrail, type BreadcrumbItem } from "@angee/ui";
import { InboxSelection } from "./documents";
import { INBOX_MODELS, type InboxNavigation } from "./state";
import { useNexusT } from "../i18n";

/** Nexus declares route ancestry; the shell owns its breadcrumb presentation. */
export function useInboxBreadcrumbs(navigation: InboxNavigation, record?: {
  message?: string;
  resultsHref?: string;
  conversation?: { id: string; label: string };
}) {
  const t = useNexusT();
  const selected = Boolean(navigation.sender || navigation.circle);
  const query = useAuthoredQuery(InboxSelection, { sender: navigation.sender, circle: navigation.circle }, {
    models: INBOX_MODELS, enabled: selected,
  });
  const selectionLabel = selected ? query.data?.inbox_selection?.label
    ?? (query.error ? t("inbox.selectionUnavailable") : t("inbox.loading")) : undefined;
  const conversationId = record?.conversation?.id;
  const conversationLabel = record?.conversation?.label;
  const messageLabel = record?.message;
  const resultsHref = record?.resultsHref;
  const items = useMemo<BreadcrumbItem[]>(() => {
    const results = { thread: undefined, at: undefined, message: undefined, part: undefined };
    const trail = [{ label: t("inbox.title"), href: navigation.href({ ...results, sender: undefined, circle: undefined }) }];
    if (selectionLabel) trail.push({ label: selectionLabel, href: resultsHref ?? navigation.href(results) });
    if (conversationId) trail.push({ label: conversationLabel && conversationLabel !== selectionLabel ? conversationLabel : t("inbox.conversation"),
      href: navigation.href({ thread: conversationId, message: undefined, part: undefined,
        at: navigation.message ? `message:${navigation.message}` : navigation.at || undefined }) });
    if (messageLabel) trail.push({ label: messageLabel, href: navigation.href({}) });
    return trail;
  }, [navigation, selectionLabel, conversationId, conversationLabel, messageLabel, resultsHref, t]);
  useBreadcrumbTrail(items);
  return selectionLabel;
}
