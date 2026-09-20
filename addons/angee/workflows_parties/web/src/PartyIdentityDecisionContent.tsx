import {
  WorkflowDecisionScaffold,
  textValue,
  type WorkflowDecisionContentProps,
} from "@angee/workflows";
import { Badge, MetaGrid, SectionEyebrow, type MetaGridRow } from "@angee/ui";
import type * as React from "react";
import * as v from "valibot";

import { useWorkflowsPartiesT } from "./i18n";

type UnknownRecord = Record<string, unknown>;
type HandleStatus = "confirmed" | "dismissed" | "suggested";
const STATUS_LABEL_KEYS = {
  confirmed: "identityReview.status.confirmed",
  dismissed: "identityReview.status.dismissed",
  suggested: "identityReview.status.suggested",
} as const satisfies Record<HandleStatus, string>;
const Text = v.optional(v.string(), "");
const Address = v.record(v.string(), v.unknown());
const Handle = v.looseObject({
  id: Text,
  value: Text,
  platform: Text,
  is_confirmed: v.optional(v.boolean(), false),
  is_dismissed: v.optional(v.boolean(), false),
});
const IdentityReviewSchema = v.looseObject({
  party_id: v.string(),
  current: v.looseObject({
    name: Text,
    addresses: v.optional(v.array(Address), []),
    handles: v.optional(v.array(Handle), []),
  }),
  proposed: v.looseObject({
    name: Text,
    address: v.optional(Address, {}),
    handle: v.optional(v.looseObject({
      party_handle_id: Text,
      value: Text,
      evidence: Text,
    }), { party_handle_id: "", value: "", evidence: "" }),
  }),
  evidence: v.optional(v.array(v.looseObject({
    label: Text,
    source_model: Text,
    source_id: Text,
  })), []),
});

/** Human-readable comparison for the shared Party identity review workflow. */
export function PartyIdentityDecisionContent(
  props: WorkflowDecisionContentProps,
): React.ReactElement {
  const t = useWorkflowsPartiesT();
  return <WorkflowDecisionScaffold
    props={props}
    schema={IdentityReviewSchema}
    actionPickerPlacement="before-content"
    header={(review) => ({
      eyebrow: t("identityReview.kind"),
      title: review.current.name || review.proposed.name || t("identityReview.unknownParty"),
      description: t("identityReview.description"),
    })}
    initialPeek={(review) => {
      const source = review.evidence.find(
        (item) => textValue(item.source_model) && textValue(item.source_id),
      );
      return source ? {
        model: source.source_model,
        id: source.source_id,
        label: source.label,
        tab: source.source_model === "workflows_extraction.Extraction" ? "evidence" : undefined,
      } : undefined;
    }}
    references={(review) => [{
      key: "party",
      label: t("identityReview.openParty"),
      reference: {
        model: "parties.Party",
        id: review.party_id,
        label: review.current.name || review.proposed.name,
      },
    }, ...review.evidence.filter(
      (item) => item.source_model && item.source_id,
    ).map((item) => ({
      key: `${item.source_model}:${item.source_id}`,
      label: item.label || t("identityReview.openEvidence"),
      kind: "evidence" as const,
      reference: {
        model: item.source_model,
        id: item.source_id,
        label: item.label,
        tab: item.source_model === "workflows_extraction.Extraction" ? "evidence" : undefined,
      },
    }))]}
  >{(review) => <PartyIdentityComparison review={review} />}</WorkflowDecisionScaffold>;
}

PartyIdentityDecisionContent.placesActionPicker = true;

function PartyIdentityComparison({ review }: {
  review: v.InferOutput<typeof IdentityReviewSchema>;
}): React.ReactElement {
  const t = useWorkflowsPartiesT();
  const currentName = review.current.name;
  const proposedName = review.proposed.name;
  const currentAddresses = review.current.addresses;
  const proposedAddress = review.proposed.address;
  const currentHandles = review.current.handles;
  const proposedHandle = review.proposed.handle;
  const proposedLink = currentHandles.find(
    (handle) => handle.id === proposedHandle.party_handle_id,
  );
  const proposedContact = proposedHandle.value || proposedLink?.value || "";
  const proposedStatus = proposedContact ? handleStatus(proposedLink ?? proposedHandle) : "";
  const proposedRows: MetaGridRow[] = [
    [t("identityReview.name"), proposedName || t("identityReview.notProvided")],
    [t("identityReview.address"), formatAddress(proposedAddress) || t("identityReview.notProvided")],
    [t("identityReview.contact"), proposedContact || t("identityReview.notProvided")],
  ];
  if (proposedStatus) {
    proposedRows.push([t("identityReview.contactStatus"), statusBadge(proposedStatus, t)]);
  }
  if (proposedHandle.evidence) {
    proposedRows.push([t("identityReview.contactEvidence"), proposedHandle.evidence]);
  }
  return <div className="space-y-4">
    <div className="grid gap-4 md:grid-cols-2">
      <IdentityPanel title={t("identityReview.current")}>
        <MetaGrid rows={[
          [t("identityReview.name"), currentName || t("identityReview.notRecorded")],
          [t("identityReview.address"), addressList(currentAddresses) || t("identityReview.notRecorded")],
        ]} />
        <ContactList handles={currentHandles} />
      </IdentityPanel>

      <IdentityPanel title={t("identityReview.proposed")}>
        <MetaGrid rows={proposedRows} />
      </IdentityPanel>
    </div>

    <p className="text-xs text-fg-muted">{t("identityReview.history")}</p>
  </div>;
}

function IdentityPanel({ children, title }: {
  children: React.ReactNode;
  title: React.ReactNode;
}): React.ReactElement {
  return <section className="space-y-3 rounded-lg border border-border-subtle p-4">
    <SectionEyebrow>{title}</SectionEyebrow>
    {children}
  </section>;
}

function ContactList({ handles }: { handles: UnknownRecord[] }): React.ReactElement {
  const t = useWorkflowsPartiesT();
  if (!handles.length) return <p className="text-13 text-fg-muted">{t("identityReview.noContacts")}</p>;
  return <ul className="space-y-2" aria-label={t("identityReview.contacts")}>
    {handles.map((handle, index) => {
      const status = handleStatus(handle);
      return <li key={`${textValue(handle.value)}:${index}`} className="flex flex-wrap items-center gap-2 text-13">
        <span className="break-all">{textValue(handle.value) || t("identityReview.notRecorded")}</span>
        {textValue(handle.platform) ? <span className="text-fg-muted">{textValue(handle.platform)}</span> : null}
        {statusBadge(status, t)}
      </li>;
    })}
  </ul>;
}

function statusBadge(status: HandleStatus, t: ReturnType<typeof useWorkflowsPartiesT>): React.ReactElement {
  const tone = status === "dismissed" ? "neutral" : status === "confirmed" ? "success" : "warning";
  return <Badge tone={tone}>{t(STATUS_LABEL_KEYS[status])}</Badge>;
}

function handleStatus(handle: UnknownRecord): HandleStatus {
  if (handle.is_dismissed === true) return "dismissed";
  if (handle.is_confirmed === true) return "confirmed";
  return "suggested";
}

function addressList(addresses: UnknownRecord[]): string {
  return addresses.map(formatAddress).filter(Boolean).join("\n\n");
}

function formatAddress(address: UnknownRecord): string {
  return ["label", "street", "extended", "po_box", "city", "region", "postal_code", "country"]
    .map((key) => textValue(address[key])).filter(Boolean).join(", ");
}
