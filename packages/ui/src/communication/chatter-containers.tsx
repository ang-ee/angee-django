import type { ReactElement } from "react";

import { EmptyState } from "../fragments/EmptyState";
import { useUiT } from "../i18n";
import type { CoreContainer } from "../runtime";

function PlaceholderLabel({ kind }: { kind: "comments" | "activity" }): ReactElement {
  const t = useUiT();
  return <>{t(kind === "comments" ? "chatter.tabComments" : "chatter.tabActivity")}</>;
}

function Placeholder({ kind }: { kind: "comments" | "activity" }): ReactElement {
  const t = useUiT();
  return kind === "comments"
    ? <EmptyState icon="comments" title={t("chatter.noComments")} description={t("chatter.commentsHint")} className="min-h-48 p-4" />
    : <EmptyState icon="activity" title={t("chatter.noActivity")} description={t("chatter.activityHint")} className="min-h-48 p-4" />;
}

/**
 * The chatter aside's container: `record#aside` children show on every record
 * view, `<model>#aside` children on that model's pages. The framework's
 * placeholder comments and activity tabs stand until an addon that supplies the
 * real ones removes them.
 */
export const CHATTER_CONTAINERS: readonly CoreContainer[] = [{
  address: "record#aside",
  models: true,
  children: {
    "chatter.comments": {
      sequence: 10,
      content: { label: <PlaceholderLabel kind="comments" />, icon: "comments", aliases: ["comments"], render: () => <Placeholder kind="comments" /> },
    },
    "chatter.activity": {
      sequence: 20,
      content: { label: <PlaceholderLabel kind="activity" />, icon: "activity", aliases: ["activity"], render: () => <Placeholder kind="activity" /> },
    },
  },
}];
