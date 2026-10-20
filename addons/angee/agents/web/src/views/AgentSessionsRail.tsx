import type { ReactElement } from "react";
import { Button, ErrorBanner, Glyph, SessionRail, SessionRailItem, SkeletonText, formatDateTime, textRoleVariants } from "@angee/ui";

import type { AcpSessionNavigation } from "../acp-session";
import { useAgentsT } from "../i18n";

/** Session navigation projects the selected transport's capabilities and Query-owned list. */
export function AgentSessionsRail({ sessions }: { sessions: AcpSessionNavigation }): ReactElement {
  const t = useAgentsT();
  return (
    <SessionRail
      label={t("sessions.sessionsLabel")}
      busy={sessions.loading}
      action={<Button size="sm" variant="ghost" onClick={sessions.create} disabled={!sessions.ready}>
        <Glyph name="plus" />{t("sessions.create")}
      </Button>}
    >
      {sessions.error ? <li><ErrorBanner description={sessions.error} actions={<Button size="sm" onClick={sessions.refresh}>{t("sessions.retry")}</Button>} /></li> : null}
      {sessions.loading ? <li><SkeletonText lines={3} /></li> : null}
      {sessions.available ? sessions.items.map((item) => (
        <SessionRailItem key={item.sessionId} active={item.sessionId === sessions.currentId}
          render={<Button type="button" variant="ghost" size="sm" className="h-auto w-full justify-start" disabled={!sessions.ready} onClick={() => sessions.select(item.sessionId, item.cwd)} />}>
          <span className="block truncate">{item.title || t("sessions.untitled")}</span>
          {item.updatedAt ? <span className={textRoleVariants({ role: "caption", truncate: true, className: "block" })}>{formatDateTime(item.updatedAt)}</span> : null}
        </SessionRailItem>
      )) : sessions.currentId ? <SessionRailItem active render={<Button type="button" variant="ghost" size="sm" className="w-full justify-start" disabled />}>
        {t("sessions.live")}
      </SessionRailItem> : null}
      {sessions.hasMore ? <li><Button size="sm" variant="ghost" disabled={sessions.loadingMore || !sessions.ready} onClick={sessions.loadMore}>
        {t("sessions.loadMore")}
      </Button></li> : null}
    </SessionRail>
  );
}
