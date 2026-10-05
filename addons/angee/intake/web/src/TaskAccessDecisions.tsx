import {
  Avatar, Badge, Card, CardContent, CardFooter, CardHeader, InlineEmpty, Skeleton, SkeletonText, TextLink,
  avatarInitials, useRouteHref, useStatusTone, type JsonValue, type StringIdRow,
} from "@angee/ui";
import type { ReactElement } from "react";

import { useIntakeT } from "./i18n";
import { TaskAccessActions } from "./TaskAccessActions";

export interface CurrentAccessRow extends StringIdRow {
  revision: number;
  permissions: readonly string[];
  claimed_name: string;
  claimed_email: string | null;
  access_verdict: JsonValue | null;
  requester_access_granted: boolean;
  party: { id: string; display_name: string } | null;
  access_decision: { id: string; verdict: JsonValue | null; is_open: boolean } | null;
}

export function TaskAccessCardSkeleton(): ReactElement {
  return <Card>
    <CardHeader className="flex-row items-center gap-2">
      <Skeleton shape="avatar" className="size-avatar-sm" />
      <Skeleton shape="text" className="w-32" />
      <Skeleton shape="text" className="w-28" />
    </CardHeader>
    <CardContent className="pt-0"><SkeletonText lines={2} /></CardContent>
    <CardFooter><Skeleton className="h-8 w-28" /></CardFooter>
  </Card>;
}

/** The pill and the sentence for each access state. */
const STATE_COPY = {
  approved: { pill: "access.state.approved", sentence: "access.copy.approved" },
  denied: { pill: "access.state.denied", sentence: "access.copy.denied" },
  pending: { pill: "access.state.pending", sentence: "access.copy.pending" },
  unavailable: { pill: "access.state.unavailable", sentence: "access.copy.unavailable" },
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
  const statusTone = useStatusTone();
  if (needs.length === 0) return <InlineEmpty label={t("access.empty")} />;

  return <div className="grid gap-3">{needs.map((need) => {
    const name = need.claimed_name || need.party?.display_name || t("access.requester");
    const email = need.claimed_email;
    const verdict = Array.isArray(need.access_verdict) ? need.access_verdict : null;
    const stateKey = verdict?.includes("intake.approve") ? "approved"
      : verdict?.includes("intake.deny") ? "denied"
        : need.access_decision?.is_open ? "pending" : "unavailable";
    const state = STATE_COPY[stateKey];
    return <Card key={need.id}>
      <CardHeader className="flex-row flex-wrap items-center gap-2">
        <Avatar size="sm" initials={avatarInitials(name)} />
        <span className="font-medium">{name}</span>
        {email ? <span className="text-fg-muted">{email}</span> : null}
        <Badge shape="pill" tone={statusTone(stateKey, { approved: "success", denied: "danger", pending: "warning" })}>
          {t(state.pill)}
        </Badge>
      </CardHeader>
      <CardContent className="pt-0 text-sm text-fg-muted">
        {t(state.sentence, { email: email || t("access.emailUnknown") })}
      </CardContent>
      <CardFooter className="flex-wrap">
        {canManage ? <TaskAccessActions need={need} /> : null}
        {need.access_decision ? <TextLink className="text-xs" href={routeHref("decisions.inbox.record", {
          id: need.access_decision.id,
        })}>{t("access.audit")}</TextLink> : null}
      </CardFooter>
    </Card>;
  })}</div>;
}
