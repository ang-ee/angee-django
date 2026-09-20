import * as React from "react";

import { usePartiesT } from "./i18n";

type UnknownRecord = Record<string, unknown>;

export interface PartyContactSummaryProps {
  party?: UnknownRecord | null;
}

function records(value: unknown): UnknownRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is UnknownRecord => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function preferredRecord(values: UnknownRecord[]): UnknownRecord | null {
  return values.find((value) => value.is_primary === true || value.is_preferred === true)
    ?? values[0]
    ?? null;
}

/** Human-readable primary address lines from the Party owner's loaded projection. */
export function partyAddressLines(party: UnknownRecord | null | undefined): string[] {
  const address = preferredRecord(records(party?.addresses));
  if (!address) return [];
  const locality = [text(address.city), text(address.region), text(address.postal_code)]
    .filter(Boolean)
    .join(", ");
  return [
    text(address.label),
    text(address.street),
    text(address.extended),
    text(address.po_box),
    locality,
    text(address.country),
  ].filter(Boolean);
}

/** Preferred email/phone values from the Party owner's loaded Handle projection. */
export function partyContactValues(party: UnknownRecord | null | undefined): {
  email: string;
  phone: string;
} {
  const handles = records(party?.handles);
  const byPlatform = (platform: string): string => {
    const matches = handles.filter((handle) => text(handle.platform).toLowerCase() === platform);
    return text(preferredRecord(matches)?.value);
  };
  return { email: byPlatform("email"), phone: byPlatform("phone") };
}

/** Compact read-only Party identity for document headers; callers provide loaded facts. */
export function PartyContactSummary({ party }: PartyContactSummaryProps): React.ReactElement | null {
  const t = usePartiesT();
  const address = partyAddressLines(party);
  const { email, phone } = partyContactValues(party);
  const name = text(party?.display_name);
  if (!name && address.length === 0 && !email && !phone) return null;
  return (
    <section
      aria-label={t("party.contact.summary")}
      className="flex flex-wrap gap-x-8 gap-y-1 text-sm text-fg-muted"
    >
      <div className="min-w-48">
        {name ? <p className="font-medium text-fg">{name}</p> : null}
        {address.length > 0 ? <address className="not-italic">{address.join(" · ")}</address> : null}
      </div>
      {email || phone ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
          {email ? <><dt>{t("party.contact.email")}</dt><dd>{email}</dd></> : null}
          {phone ? <><dt>{t("party.contact.phone")}</dt><dd>{phone}</dd></> : null}
        </dl>
      ) : null}
    </section>
  );
}
