import { useModelMetadata } from "@angee/metadata";
import { TextLink, useRelationSelectedOption, useResourceRecordHrefLookup } from "@angee/ui";
import { relationFieldInfoForResource } from "@angee/ui/views/model-metadata-defaults";

/** Resolve the record's display representation through the shared metadata and read owners. */
export function DecisionSubject({ model, id }: { model: string; id: string }) {
  const metadata = useModelMetadata(model);
  const selected = useRelationSelectedOption(relationFieldInfoForResource(model, metadata), id);
  const recordHref = useResourceRecordHrefLookup();
  const href = recordHref(model, id);
  const label = selected?.value === id ? selected.label : id;
  return href ? <TextLink href={href}>{label}</TextLink> : <>{label}</>;
}
