import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  EmptyState,
  ErrorBanner,
  LoadingPanel,
  errorMessage,
  recordTargetSearch,
  useRouteHref,
  useRouteParam,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { PartyHandleRedirectDocument } from "./documents";
import { usePartiesT } from "./i18n";

/** Open a retained handle association at its owning Party identity surface. */
export function PartyHandleRedirect(): React.ReactElement {
  const t = usePartiesT();
  const id = useRouteParam("id") ?? "";
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const query = useAuthoredQuery(
    PartyHandleRedirectDocument,
    { id },
    { enabled: Boolean(id), models: ["parties.PartyHandle"] },
  );
  const association = query.data?.party_handles_by_pk;

  React.useEffect(() => {
    if (!association?.party?.id) return;
    void navigate({
      to: routeHref("parties.records.record", { id: association.party.id }),
      replace: true,
      state: true,
      search: (current: Record<string, unknown>) => ({
        ...recordTargetSearch(current, { tab: "identity" }),
        partyHandle: association.id,
      }),
    });
  }, [association, navigate, routeHref]);

  if (query.error) {
    return <ErrorBanner description={errorMessage(query.error, t("partyRedirect.unavailable"))} />;
  }
  if (id && query.isFetching) return <LoadingPanel message={t("partyRedirect.loading")} />;
  return <EmptyState icon="handle" title={t("partyRedirect.unavailable")} />;
}
