import type { DocumentType } from "@angee/gql/console";
import { Avatar, Card, CardContent, CardFooter, CardHeader, InlineEmpty, NavLink, StatusBadge, avatarInitials, textRoleVariants, useRouteHref } from "@angee/ui";
import type { ReactElement } from "react";

import type { TaskAccessNeedsDocument } from "./documents";
import { useIntakeT } from "./i18n";
import { TaskAccessActions } from "./TaskAccessActions";

/** One Need as the Task's access query selects it. */
export type CurrentAccessRow = DocumentType<typeof TaskAccessNeedsDocument>["intake_needs"][number];

/** The pill and the sentence for each access state. */
const STATE_COPY = {
  approved: { pill: "access.state.approved", sentence: "access.copy.approved" },
  denied: { pill: "access.state.denied", sentence: "access.copy.denied" },
  pending: { pill: "access.state.pending", sentence: "access.copy.pending" },
  unknown: { pill: "access.state.unknown", sentence: "access.copy.unknown" },
} as const;

/** Render the Need owner's current access decision without another collection surface. */
export function TaskAccessDecisions({
  needs, canManage = false,
}: {
  needs: readonly CurrentAccessRow[];
  canManage?: boolean;
}): ReactElement {
  const t = useIntakeT();
  const routeHref = useRouteHref();
  if (needs.length === 0) return <InlineEmpty label={t("access.empty")} />;

  return <div className="grid gap-3">{needs.map((need) => {
    const name = need.claimed_name || need.party?.display_name || t("access.requester");
    const email = need.claimed_email;
    const verdict = Array.isArray(need.access_verdict) ? need.access_verdict : null;
    const stateKey = verdict?.includes("intake.approve") ? "approved"
      : verdict?.includes("intake.deny") ? "denied"
        : need.access_decision?.is_open ? "pending" : "unknown";
    const state = STATE_COPY[stateKey];
    return <Card key={need.id}>
      <CardHeader className="flex-row flex-wrap items-center gap-2">
        <Avatar size="sm" src={need.party?.display_avatar?.url || undefined} initials={avatarInitials(name)} />
        <span className="font-medium">{name}</span>
        {email ? <span className="text-fg-muted">{email}</span> : null}
        {/* approved, denied, pending and unknown read their tones from the framework vocabulary. */}
        <StatusBadge value={stateKey}>{t(state.pill)}</StatusBadge>
      </CardHeader>
      <CardContent className={textRoleVariants({ role: "meta", className: "pt-0" })}>
        {t(state.sentence, { email: email || t("access.emailUnknown") })}
      </CardContent>
      <CardFooter className="flex-wrap">
        {canManage ? <TaskAccessActions need={need} /> : null}
        {need.access_decision ? <NavLink className="text-12" href={routeHref("decisions.inbox.record", {
          id: need.access_decision.id,
        })} variant="inline">{t("access.audit")}</NavLink> : null}
      </CardFooter>
    </Card>;
  })}</div>;
}
