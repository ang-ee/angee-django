import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { EmptyState, LoadingPanel, useRouteHref, useRouteParam } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { PartyRecordRedirectDocument } from "./documents";
import { usePartiesT } from "./i18n";

export function PartyRecordRedirect(): React.ReactElement {
  const t = usePartiesT();
  const id = useRouteParam("id") ?? "";
  const query = useAuthoredQuery(PartyRecordRedirectDocument, { id }, { models: ["parties.Party"], enabled: Boolean(id) });
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const party = query.data?.parties_by_pk;
  const route = party?.concrete_kind === "person"
    ? "parties.people.record"
    : party?.concrete_kind === "organization"
      ? "parties.organizations.record"
      : null;
  React.useEffect(() => {
    if (!id) {
      void navigate({ to: routeHref("parties.overview"), replace: true });
      return;
    }
    if (!route || !party) return;
    void navigate({
      to: routeHref(route, { id: party.id }),
      replace: true,
      search: (current: Record<string, unknown>) => current,
    });
  }, [id, navigate, party, route, routeHref]);
  if (query.isFetching) return <LoadingPanel message={t("partyRedirect.loading")} />;
  return <EmptyState icon="parties" title={t("partyRedirect.unavailable")} />;
}
