import type { ReactElement } from "react";

import { EmptyState } from "../fragments/EmptyState";
import { useUiT } from "../i18n";
import type { CoreContainer } from "../runtime";

function PlaceholderLabel({ kind }: { kind: "comments" | "history" }): ReactElement {
  const t = useUiT();
  return <>{t(kind === "comments" ? "chatter.tabComments" : "chatter.tabHistory")}</>;
}

function Placeholder({ kind }: { kind: "comments" | "history" }): ReactElement {
  const t = useUiT();
  return kind === "comments"
    ? <EmptyState icon="comments" title={t("chatter.noComments")} description={t("chatter.commentsHint")} className="min-h-48 p-4" />
    : <EmptyState icon="activity" title={t("chatter.noHistory")} description={t("chatter.historyHint")} className="min-h-48 p-4" />;
}

/**
 * The chatter aside's container: `record#aside` children show on every record
 * view, `<model>#aside` children on that model's pages. The framework's
 * placeholder comments and history tabs stand until an addon that supplies the
 * real ones removes them.
 */
export const CHATTER_CONTAINERS: readonly CoreContainer[] = [{
  address: "record#aside",
  models: true,
  // Pages publish their own tabs at render time; `only` may name them.
  extras: true,
  children: {
    "chatter.comments": {
      sequence: 10,
      content: { label: <PlaceholderLabel kind="comments" />, icon: "comments", aliases: ["comments"], render: () => <Placeholder kind="comments" /> },
    },
    "chatter.history": {
      sequence: 20,
      content: {
        // "activity" and "chatter.activity" are the former ids, kept in `?chatterTab=` links until angee 0.3.0
        // removes them (.changeset/chatter-history-tab.md).
        label: <PlaceholderLabel kind="history" />, icon: "activity", aliases: ["history", "activity", "chatter.activity"],
        render: () => <Placeholder kind="history" />,
      },
    },
  },
}];
