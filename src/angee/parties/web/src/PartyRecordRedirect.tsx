import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { EmptyState, LoadingPanel, useRouteHref, useRouteParam } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { PartyRecordRedirectDocument } from "./documents";
import { usePartiesT } from "./i18n";

export interface PartyRecordRedirectProps {
  /**
   * A party id a concrete record page read no record for: it is followed only to a different
   * party (the survivor a merge folded it into), never back to the page that missed.
   */
  id?: string;
  /** What the page shows when that id leads nowhere else: the record form's own unavailable state. */
  unavailable?: React.ReactElement;
}

/**
 * Open the concrete page of the live party a party id names now (`party_record`): the generic
 * party record route, and a person's or organization's page whose own read missed.
 */
export function PartyRecordRedirect({ id: missed, unavailable }: PartyRecordRedirectProps = {}): React.ReactElement {
  const t = usePartiesT();
  const routeId = useRouteParam("id") ?? "";
  const id = missed ?? routeId;
  const query = useAuthoredQuery(PartyRecordRedirectDocument, { id }, { models: ["parties.Party"], enabled: Boolean(id) });
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  // The live party the id names now: itself, or the survivor a merge folded it into. A missed id
  // that resolves to itself would only miss again.
  const resolved = query.data?.party_record;
  const party = resolved && resolved.id !== missed ? resolved : null;
  const route = party?.concrete_kind === "PERSON"
    ? "parties.people.record"
    : party?.concrete_kind === "ORGANIZATION"
      ? "parties.organizations.record"
      : null;
  React.useEffect(() => {
    if (!id) {
      void navigate({ to: routeHref("parties.overview"), replace: true, state: true });
      return;
    }
    if (!route || !party) return;
    void navigate({
      to: routeHref(route, { id: party.id }),
      replace: true,
      state: true,
      search: (current: Record<string, unknown>) => current,
    });
  }, [id, navigate, party, route, routeHref]);
  if (query.isFetching || (route && party)) return <LoadingPanel loadingLabel={t("partyRedirect.loading")} />;
  return unavailable ?? <EmptyState icon="parties" title={t("partyRedirect.unavailable")} />;
}

/**
 * The person and organization forms' `renderUnavailable`: an id their page read no record for
 * opens the live party it names now, if that is another; otherwise the form's unavailable state.
 */
export function renderPartyUnavailable(id: string, unavailable: React.ReactElement): React.ReactNode {
  return <PartyRecordRedirect id={id} unavailable={unavailable} />;
}
