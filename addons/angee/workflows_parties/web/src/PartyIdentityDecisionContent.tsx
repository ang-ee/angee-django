import type { DecisionContentProps } from "@angee/decisions";
import { partyAddressText } from "@angee/parties";
import { Badge, ComparisonRows, DetailSection, EmptyState, MetaGrid } from "@angee/ui";
import type { ReactElement } from "react";
import * as v from "valibot";

import { useWorkflowsPartiesT } from "./i18n";

const Address = v.record(v.string(), v.unknown());
const Handle = v.object({
  id: v.string(),
  platform: v.string(),
  value: v.string(),
  is_confirmed: v.boolean(),
  is_dismissed: v.boolean(),
});
const IdentityBasis = v.object({
  party_id: v.string(),
  current: v.object({ name: v.string(), addresses: v.array(Address), handles: v.array(Handle) }),
  proposed: v.object({
    name: v.string(),
    address: Address,
    handle: v.object({ party_handle_id: v.string(), evidence: v.string() }),
  }),
});

/** Present the retained basis; the inbox owns actions, evidence links, and submission. */
export function PartyIdentityDecisionContent({ basis }: Pick<DecisionContentProps, "basis">): ReactElement {
  const t = useWorkflowsPartiesT();
  const parsed = v.safeParse(IdentityBasis, basis);
  if (!parsed.success) return <EmptyState title={t("review.unavailable")} description={t("review.unavailableDescription")} />;
  const { current, proposed } = parsed.output;
  const proposedContact = current.handles.find((handle) => handle.id === proposed.handle.party_handle_id);
  return <DetailSection title={t("identity.title")}>
    <ComparisonRows beforeLabel={t("identity.current")} afterLabel={t("identity.proposed")} rows={[
      { key: "name", label: t("identity.name"), before: current.name || t("identity.notRecorded"), after: proposed.name || t("identity.notProvided") },
      { key: "address", label: t("identity.address"), before: current.addresses.map(partyAddressText).filter(Boolean).join("; ") || t("identity.notRecorded"), after: partyAddressText(proposed.address) || t("identity.notProvided") },
      { key: "contact", label: t("identity.contact"), before: current.handles.length ? <ul aria-label={t("identity.contacts")} className="space-y-2">
        {current.handles.map((handle) => <li key={handle.id} className="flex flex-wrap items-center gap-2">
          <span>{handle.value}</span><span className="text-fg-muted">{handle.platform}</span>
          <Badge tone={handle.is_dismissed ? "neutral" : handle.is_confirmed ? "success" : "warning"}>
            {t(handle.is_dismissed ? "identity.dismissed" : handle.is_confirmed ? "identity.confirmed" : "identity.suggested")}
          </Badge>
        </li>)}
      </ul> : t("identity.notRecorded"), after: proposedContact?.value || t("identity.notProvided"),
      details: proposed.handle.evidence ? <MetaGrid rows={[[t("identity.contactEvidence"), proposed.handle.evidence]]} /> : undefined },
    ]} />
  </DetailSection>;
}
