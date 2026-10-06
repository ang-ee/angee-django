import type { ReactElement } from "react";
import { useModelMetadata } from "@angee/metadata";

import { useResourceRecordHrefLookup } from "../../runtime";
import { ChipList } from "../../ui/chip";
import { TextLink } from "../../ui/text-link";
import { relationFieldInfoForResource } from "../resource/model-metadata-defaults";
import { recordTargetHref, type RecordTargetSearch } from "../resource/record-navigation-context";
import { useRelationSelectedOption } from "./relation-options";

export interface RecordReferenceProps extends RecordTargetSearch {
  model: string;
  id: string;
  /** A retained display label avoids another record read. */
  label?: string;
}

/** Display a record through its metadata and follow its registered record route. */
export function RecordReference({ model, id, label, tab, search }: RecordReferenceProps): ReactElement {
  const recordHref = useResourceRecordHrefLookup();
  const content = label || <RecordReferenceLabel model={model} id={id} />;
  const href = recordHref(model, id);
  const className = "max-w-full whitespace-normal text-left leading-snug [overflow-wrap:anywhere]";
  return href ? <TextLink href={recordTargetHref(href, { tab, search })} className={className}>{content}</TextLink> : <span className={className}>{content}</span>;
}

/** Related records as one chip each, every chip a {@link RecordReference}. */
export function RecordReferenceChips({ model, records }: {
  model: string;
  records: readonly { id: string; label?: string }[];
}): ReactElement {
  return <ChipList items={records.map(({ id, label }) => ({
    id, label: <RecordReference model={model} id={id} label={label} />,
  }))} />;
}

function RecordReferenceLabel({ model, id }: Pick<RecordReferenceProps, "model" | "id">): ReactElement {
  const metadata = useModelMetadata(model);
  const selected = useRelationSelectedOption(relationFieldInfoForResource(model, metadata), id);
  return <>{selected?.value === id ? selected.label : id}</>;
}
