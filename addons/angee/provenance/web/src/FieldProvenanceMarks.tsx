import { useMemo } from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  useRecordChromeContext,
  useRecordFieldMarks,
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

/** Publish statement-owned provenance into the one shared form-field decoration surface. */
export function FieldProvenanceMarks() {
  const { canonicalResource, dataProviderName, recordId } = useRecordChromeContext();
  const t = useProvenanceT();
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
    marks: (query.data?.field_provenance ?? []).map((provenance) => provenanceMark(provenance, t)),
  }], [canonicalResource, query.data?.field_provenance, recordId, t]);
  useRecordFieldMarks(publications);
  return null;
}

function provenanceMark(provenance: FieldProvenance, t: ProvenanceT): RecordFieldMark {
  const presentation = AUTHORITY_PRESENTATION[provenance.authority];
  return {
    field: provenance.field,
    label: t(presentation.key),
    tone: presentation.tone,
    origin: provenance.origin,
    target: { model: provenance.statement_model, id: provenance.statement_id },
  };
}
