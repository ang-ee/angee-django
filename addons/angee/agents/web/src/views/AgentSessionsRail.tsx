import type { ReactElement } from "react";
import { Button, ErrorBanner, Glyph, SessionRail, SessionRailItem, Skeleton, formatDateTime } from "@angee/ui";

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
      {sessions.loading ? Array.from({ length: 3 }, (_, index) => <li key={index}><Skeleton className="h-8" /></li>) : null}
      {sessions.available ? sessions.items.map((item) => (
        <SessionRailItem key={item.sessionId} active={item.sessionId === sessions.currentId}
          render={<button type="button" disabled={!sessions.ready} onClick={() => sessions.select(item.sessionId, item.cwd)} />}>
          <span className="block truncate">{item.title || t("sessions.untitled")}</span>
          {item.updatedAt ? <span className="block truncate text-12 text-fg-muted">{formatDateTime(new Date(item.updatedAt))}</span> : null}
        </SessionRailItem>
      )) : sessions.currentId ? <SessionRailItem active render={<button type="button" disabled />}>
        {t("sessions.live")}
      </SessionRailItem> : null}
      {sessions.hasMore ? <li><Button size="sm" variant="ghost" disabled={sessions.loadingMore || !sessions.ready} onClick={sessions.loadMore}>
        {t("sessions.loadMore")}
      </Button></li> : null}
    </SessionRail>
  );
}
