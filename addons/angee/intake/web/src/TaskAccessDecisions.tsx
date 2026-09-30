import {
  Avatar, Badge, Card, CardContent, CardFooter, CardHeader, InlineEmpty, Skeleton, SkeletonText, TextLink,
  avatarInitials, useRouteHref, useStatusTone, type StringIdRow,
} from "@angee/ui";
import type { ReactElement } from "react";

import { useIntakeT } from "./i18n";
import { TaskAccessActions } from "./TaskAccessActions";

export interface CurrentAccessRow extends StringIdRow {
  revision: number;
  permissions: readonly string[];
  claimed_name: string;
  claimed_email: string | null;
  access_verdict: string | null;
  party: { id: string; display_name: string } | null;
  access_decision: { id: string; verdict: string; is_open: boolean } | null;
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
    const verdict = need.access_verdict?.toUpperCase();
    const state = verdict === "COMPLETED" ? "approved"
      : verdict === "REJECTED" ? "denied"
        : need.access_decision?.is_open ? "pending" : "unavailable";
    return <Card key={need.id}>
      <CardHeader className="flex-row flex-wrap items-center gap-2">
        <Avatar size="sm" initials={avatarInitials(name)} />
        <span className="font-medium">{name}</span>
        {email ? <span className="text-fg-muted">{email}</span> : null}
        <Badge shape="pill" tone={statusTone(need.access_verdict, { REJECTED: "danger" })}>
          {t(`access.state.${state}`)}
        </Badge>
      </CardHeader>
      <CardContent className="pt-0 text-sm text-fg-muted">
        {t(`access.copy.${state}`, { email: email || t("access.emailUnknown") })}
      </CardContent>
      <CardFooter className="flex-wrap">
        {canManage ? <TaskAccessActions need={need} /> : null}
        {need.access_decision ? <TextLink className="text-xs" href={routeHref("decisions.decisions.record", {
          id: need.access_decision.id,
        })}>{t("access.audit")}</TextLink> : null}
      </CardFooter>
    </Card>;
  })}</div>;
}
