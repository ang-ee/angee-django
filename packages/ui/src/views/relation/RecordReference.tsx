import { useMemo, type ReactElement, type ReactNode } from "react";
import { useModelMetadata } from "@angee/metadata";

import { makeContext, useContainer, useResourceRecordHrefLookup, type CoreContainer } from "../../runtime";
import { ContainerOutlet, containerHasContent } from "../../lib/container-outlet";
import { PaneRow, type PaneRowProps } from "../../communication/pane-list";
import { ChipList } from "../../ui/chip";
import { NavLink } from "../../ui/nav-link";
import { relationFieldInfoForResource } from "../resource/model-metadata-defaults";
import { recordTargetHref, type RecordTargetSearch } from "../resource/record-navigation-context";
import { useRelationSelectedOption, useRelationSelectedOptions } from "./relation-options";

export interface RecordReferenceProps extends RecordTargetSearch {
  model: string;
  id: string;
  /** A retained display label avoids another record read. */
  label?: ReactNode;
}

/** Display a record through its metadata and follow its registered record route.
 * @kind fragment
 */
export function RecordReference({ model, id, label, tab, search }: RecordReferenceProps): ReactElement {
  const recordHref = useResourceRecordHrefLookup();
  const content = label || <RecordReferenceLabel model={model} id={id} />;
  const href = recordHref(model, id);
  const className = "max-w-full whitespace-normal text-left leading-snug [overflow-wrap:anywhere]";
  return href ? <NavLink href={recordTargetHref(href, { tab, search })} variant="inline" className={className}>{content}</NavLink> : <span className={className}>{content}</span>;
}

/** Opt-in record previews contributed by the record's addon.
 * @kind contribution
 */
export const REFERENCE_CONTAINERS: readonly CoreContainer[] = [
  { address: "reference#preview", models: true },
];

const ReferenceContext = makeContext<RecordReferenceProps>("RecordReferencePreview");

/** Identity and navigation supplied to a model's preview contribution.
 * @kind null
 * @reason Runtime context for an addon-owned record preview.
 */
export function useRecordReference(): RecordReferenceProps {
  return ReferenceContext.use();
}

/** Show the owning addon's preview when contributed, otherwise its canonical link.
 * @owner RecordReference
 */
export function RecordReferencePreview(props: RecordReferenceProps): ReactElement {
  const models = useMemo(() => [props.model], [props.model]);
  const entries = useContainer("reference#preview", { models });
  return <ReferenceContext.Provider value={props}>
    {containerHasContent(entries) ? <ContainerOutlet entries={entries} /> : <RecordReference {...props} />}
  </ReferenceContext.Provider>;
}

export interface RecordReferenceRowProps extends Pick<RecordReferenceProps, "model" | "id" | "tab" | "search">,
  Pick<PaneRowProps, "icon" | "detail" | "meta" | "sub"> {
  /** All references in this list. Rows of one model share one bounded, cached label read. */
  references: readonly Pick<RecordReferenceProps, "model" | "id">[];
}

/** A record pointer with metadata-owned labels, batched per model without changing list order.
 * @owner RecordReference
 */
export function RecordReferenceRow({ model, id, references, tab, search, icon = "link", ...props }: RecordReferenceRowProps): ReactElement {
  const metadata = useModelMetadata(model);
  const ids = useMemo(() => [...new Set(references.filter((reference) => reference.model === model)
    .map((reference) => reference.id))].sort(), [model, references]);
  const options = useRelationSelectedOptions(relationFieldInfoForResource(model, metadata), ids, []);
  const href = useResourceRecordHrefLookup()(model, id);
  return <PaneRow {...props} icon={icon} title={options.find((option) => option.value === id)?.label ?? id}
    href={href ? recordTargetHref(href, { tab, search }) : undefined} />;
}

/** Related records as one chip each, every chip a {@link RecordReference}. */
export function RecordReferenceChips({ model, records }: {
  model: string;
  /** Each record's id, its label when loaded, and its own `#rrggbb` colour when its model has one. */
  records: readonly { id: string; label?: string; color?: string }[];
}): ReactElement {
  return <ChipList items={records.map(({ id, label, color }) => ({
    id, label: <RecordReference model={model} id={id} label={label} />, color,
  }))} />;
}

function RecordReferenceLabel({ model, id }: Pick<RecordReferenceProps, "model" | "id">): ReactElement {
  const metadata = useModelMetadata(model);
  const selected = useRelationSelectedOption(relationFieldInfoForResource(model, metadata), id);
  return <>{selected?.value === id ? selected.label : id}</>;
}
