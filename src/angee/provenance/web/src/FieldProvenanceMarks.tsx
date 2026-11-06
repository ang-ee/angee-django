import { useCallback, useMemo } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { useModelMetadata } from "@angee/metadata";
import {
  useRecordChromeContext,
  useRecordFieldMarks,
  useChatter,
  type RecordFieldMark,
} from "@angee/ui";

import { FieldProvenanceDocument, type FieldProvenance } from "./documents.console";
import { useProvenanceT, type ProvenanceT } from "./i18n";

const AUTHORITY_PRESENTATION = {
  SOURCE: { key: "fieldProvenance.source", tone: "info" },
  CORRECTION: { key: "fieldProvenance.correction", tone: "success" },
  UNVERIFIED: { key: "fieldProvenance.unverified", tone: "warning" },
} as const satisfies Record<FieldProvenance["authority"], {
  key: "fieldProvenance.source" | "fieldProvenance.correction" | "fieldProvenance.unverified";
  tone: RecordFieldMark["tone"];
}>;

/** Mount provenance only for records declaring field statements. */
export function FieldProvenanceChrome() {
  const { canonicalResource } = useRecordChromeContext();
  const hasFieldStatements = Boolean(useModelMetadata(canonicalResource)?.resource.fieldProvenance);
  return hasFieldStatements ? <FieldProvenanceMarks /> : null;
}

/** Publish statement-owned provenance into the one shared form-field decoration surface. */
export function FieldProvenanceMarks() {
  const { canonicalResource, dataProviderName, recordId } = useRecordChromeContext();
  const { setActiveTab, setCollapsed } = useChatter();
  const t = useProvenanceT();
  const hasHistory = Boolean(useModelMetadata(canonicalResource)?.resource.history);
  const revealChangeset = useCallback((changeset: string) => {
    setActiveTab("provenance.history", { target: changeset, focus: "changeset" });
    setCollapsed(false);
  }, [setActiveTab, setCollapsed]);
  const query = useAuthoredQuery(
    FieldProvenanceDocument,
    { model: canonicalResource, id: recordId },
    {
      ...(dataProviderName !== undefined ? { dataProviderName } : {}),
      models: [canonicalResource],
      records: [{ model: canonicalResource, id: recordId }],
    },
  );
  const publications = useMemo(() => [{
    model: canonicalResource,
    id: recordId,
    marks: (query.data?.field_provenance ?? []).map((provenance) => provenanceMark(provenance, t, hasHistory ? revealChangeset : undefined)),
  }], [canonicalResource, query.data?.field_provenance, hasHistory, recordId, revealChangeset, t]);
  useRecordFieldMarks(publications);
  return null;
}

function provenanceMark(provenance: FieldProvenance, t: ProvenanceT, revealChangeset?: (id: string) => void): RecordFieldMark {
  const presentation = AUTHORITY_PRESENTATION[provenance.authority];
  const changeset = provenance.changeset_id;
  return {
    field: provenance.field,
    label: t(presentation.key),
    tone: presentation.tone,
    origin: provenance.origin,
    ...(changeset && revealChangeset ? { onReveal: () => revealChangeset(changeset) } : {}),
  };
}
