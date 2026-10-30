import * as React from "react";
import { RecordReference, StatusBadge, formatNumber, type ListColumn, type StringIdRow, type WidgetOption } from "@angee/ui";

import { usePartiesT } from "./i18n";

export interface IdentityClaimRow extends StringIdRow {
  confidence?: number;
  is_confirmed?: boolean;
  is_dismissed?: boolean;
  evidence?: Array<{ model: string; id: string }>;
  evidence_truncated?: boolean;
}

/** The claim's state word: the statement's two flags fold into one status value. */
export function identityClaimState(row: Pick<IdentityClaimRow, "is_confirmed" | "is_dismissed">): "dismissed" | "confirmed" | "suggested" {
  if (row.is_dismissed) return "dismissed";
  if (row.is_confirmed) return "confirmed";
  return "suggested";
}

/**
 * The columns every identity-claim list shares (a party's Identity tab, the review
 * queue): a 0–1 confidence as a percentage, the source evidence as record
 * references, and the claim state as a status badge.
 */
export function useIdentityClaimColumns<TRow extends IdentityClaimRow>(): {
  confidence: ListColumn<TRow>;
  evidence: ListColumn<TRow>;
  state: ListColumn<TRow>;
} {
  const t = usePartiesT();
  return React.useMemo(() => {
    const stateOptions: readonly WidgetOption[] = [
      { value: "confirmed", label: t("identity.state.confirmed") },
      { value: "suggested", label: t("identity.state.suggested") },
      { value: "dismissed", label: t("identity.state.dismissed") },
    ];
    return {
      confidence: {
        field: "confidence",
        header: t("identity.confidence"),
        align: "right",
        render: (row) => typeof row.confidence === "number"
          ? formatNumber(row.confidence, { style: "percent", maximumFractionDigits: 0 })
          : null,
      },
      evidence: {
        field: "evidence",
        header: t("identity.evidence"),
        render: (row) => row.evidence?.length
          ? <span className="flex flex-wrap gap-2">
            {row.evidence.map((ref, index) => <RecordReference key={`${ref.model}:${ref.id}`} model={ref.model} id={ref.id}
              label={t("identity.evidenceSource", { number: index + 1 })} />)}
            {row.evidence_truncated ? <span className="text-fg-muted">{t("identity.evidenceTruncated")}</span> : null}
          </span>
          : t("identity.evidenceUnavailable"),
      },
      state: {
        field: "is_confirmed",
        header: t("identity.state"),
        render: (row) => <StatusBadge value={identityClaimState(row)} options={stateOptions} />,
      },
    };
  }, [t]);
}
