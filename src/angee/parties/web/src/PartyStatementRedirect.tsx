import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  EmptyState,
  ErrorBanner,
  LoadingPanel,
  recordTargetSearch,
  useRouteHref,
  useRouteParam,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { PartyStatementRedirectDocument } from "./documents";
import { usePartiesT } from "./i18n";

/** Open a retained handle association at its owning Party identity surface. */
export function PartyStatementRedirect(): React.ReactElement {
  const t = usePartiesT();
  const id = useRouteParam("id") ?? "";
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const query = useAuthoredQuery(
    PartyStatementRedirectDocument,
    { id },
    { enabled: Boolean(id), models: ["parties.PartyStatement"] },
  );
  const association = query.data?.party_statements_by_pk;

  React.useEffect(() => {
    if (!association?.subject?.id) return;
    void navigate({
      to: routeHref("parties.records.record", { id: association.subject.id }),
      replace: true,
      state: true,
      search: (current: Record<string, unknown>) => ({
        ...recordTargetSearch(current, { tab: "identity" }),
        partyStatement: association.id,
      }),
    });
  }, [association, navigate, routeHref]);

  if (query.error) {
    return <ErrorBanner description={t("partyRedirect.unavailable")} />;
  }
  if (id && query.isFetching) return <LoadingPanel message={t("partyRedirect.loading")} />;
  return <EmptyState icon="handle" title={t("partyRedirect.unavailable")} />;
}
