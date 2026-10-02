import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import { EmptyState, LoadingPanel, registerForm, type RegisteredFormProps } from "@angee/ui";

import { PartyRecordRedirectDocument } from "./documents";
import { usePartiesT } from "./i18n";
import { OrganizationForm } from "./OrganizationForm";
import { PersonForm } from "./PersonForm";

const MODEL = "parties.Party";

/** Resolve the polymorphic Party parent to its one canonical concrete form. */
export function PartyForm(props: RegisteredFormProps): React.ReactElement {
  const t = usePartiesT();
  const id = props.id ?? "";
  const query = useAuthoredQuery(
    PartyRecordRedirectDocument,
    { id },
    { models: [MODEL], enabled: Boolean(id) },
  );
  const party = query.data?.parties_by_pk;
  // Only block on the first load; keep the resolved concrete form mounted across
  // background refetches so in-flight edits, the active tab, and scroll survive.
  if (query.isFetching && !party) return <LoadingPanel message={t("partyRedirect.loading")} />;
  if (party?.concrete_kind === "ORGANIZATION") {
    return <OrganizationForm {...props} resource="parties.Organization" />;
  }
  if (party?.concrete_kind === "PERSON") {
    return <PersonForm {...props} resource="parties.Person" />;
  }
  return <EmptyState icon="parties" title={t("partyRedirect.unavailable")} />;
}

export const partyForm = registerForm(MODEL, PartyForm);
