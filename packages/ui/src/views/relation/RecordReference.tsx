import type { ReactElement } from "react";
import { useModelMetadata } from "@angee/metadata";
import { useNavigate } from "@tanstack/react-router";

import { useResourceRecordHrefLookup } from "../../runtime";
import { Button } from "../../ui/button";
import { TextLink } from "../../ui/text-link";
import { relationFieldInfoForResource } from "../resource/model-metadata-defaults";
import { useRelationSelectedOption } from "./relation-options";

export interface RecordReferenceProps {
  model: string;
  id: string;
  /** A retained display label avoids another record read. */
  label?: string;
  /** An owning surface may open a record peek instead of following its route. */
  onOpen?: () => void;
}

/** Display a record through its metadata and follow its registered record route. */
export function RecordReference({ model, id, label, onOpen }: RecordReferenceProps): ReactElement {
  const recordHref = useResourceRecordHrefLookup();
  const navigate = useNavigate();
  const content = label || <RecordReferenceLabel model={model} id={id} />;
  if (onOpen) return <Button type="button" size="sm" variant="ghost" className="h-auto min-h-btn-sm max-w-full whitespace-normal py-1 text-left leading-snug [overflow-wrap:anywhere]" onClick={onOpen}>{content}</Button>;
  const href = recordHref(model, id);
  // Follow the route in-app; a plain anchor would reload the whole console.
  return href ? <TextLink href={href} onNavigate={(to) => void navigate({ href: to })} className="[overflow-wrap:anywhere]">{content}</TextLink> : <span className="[overflow-wrap:anywhere]">{content}</span>;
}

function RecordReferenceLabel({ model, id }: Pick<RecordReferenceProps, "model" | "id">): ReactElement {
  const metadata = useModelMetadata(model);
  const selected = useRelationSelectedOption(relationFieldInfoForResource(model, metadata), id);
  return <>{selected?.value === id ? selected.label : id}</>;
}
