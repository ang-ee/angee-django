import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { ControlBandProvider } from "../layouts/ControlBand";
import { useResourceRecordHrefLookup } from "../runtime";
import { Button } from "../ui/button";
import { TextLink } from "../ui/text-link";
import { FormView } from "../views/form/FormView";
import { RegisteredFormView, useRegisteredForm } from "../views/form/registered-form";
import { recordTargetHref } from "../views/resource/record-navigation-context";
import { useChatter, useChatterContent } from "./chatter-context";

/** Presentation coordinates only. Data and permissions remain with the record. */
export interface RecordPeekReference {
  model: string;
  id: string;
  label?: string;
  tab?: string | null;
  page?: number | null;
  /** Detail state declared and parsed by the target record's owning addon. */
  search?: Readonly<Record<string, string | null>>;
}

interface RecordPeekContextValue {
  reference: RecordPeekReference;
  openRecord: (reference: RecordPeekReference) => void;
}

const RecordPeekContext = React.createContext<RecordPeekContextValue | null>(null);

/** Record-owned sections can follow sources in the same peek breadcrumb. */
export function useRecordPeekContext(): RecordPeekContextValue | null {
  return React.useContext(RecordPeekContext);
}

/** Publish a readonly native record form into Chatter or a record's right aside. */
export function useRecordPeek(): (reference: RecordPeekReference) => void {
  const t = useUiT();
  const { recordSupportKey, setRecordPreview, setActiveTab, setCollapsed } = useChatter();
  const ownerRef = React.useRef<symbol | null>(null);
  if (ownerRef.current === null) ownerRef.current = Symbol("record-preview");
  const owner = ownerRef.current;
  const [state, setState] = React.useState<{
    key: string | null;
    references: readonly RecordPeekReference[];
  }>({ key: recordSupportKey, references: [] });
  const references = state.key === recordSupportKey ? state.references : [];
  const openRecord = React.useCallback((reference: RecordPeekReference) => {
    setState((current) => {
      const records = current.key === recordSupportKey ? current.references : [];
      const previous = records.at(-1);
      if (previous && sameRecordPeekReference(previous, reference)) return current;
      return { key: recordSupportKey, references: [...records, reference] };
    });
    if (recordSupportKey === null) setActiveTab("records");
    setCollapsed(false);
  }, [recordSupportKey, setActiveTab, setCollapsed]);
  const openSource = React.useCallback((reference: RecordPeekReference) => {
    if (recordSupportKey === null) {
      openRecord(reference);
      return;
    }
    setState((current) => current.key === recordSupportKey
      && current.references.length === 1
      && sameRecordPeekReference(current.references[0]!, reference)
        ? current
        : { key: recordSupportKey, references: [reference] });
    setCollapsed(false);
  }, [openRecord, recordSupportKey, setCollapsed]);
  const goBack = React.useCallback((index: number) => {
    setState((current) => current.key === recordSupportKey
      ? { ...current, references: current.references.slice(0, index + 1) }
      : current);
  }, [recordSupportKey]);
  const close = React.useCallback(() => {
    setState({ key: recordSupportKey, references: [] });
  }, [recordSupportKey]);
  const content = React.useMemo(() => recordSupportKey === null && references.length ? {
    tabs: [{
      id: "records",
      label: t("chatter.tabRecords"),
      icon: "file",
      panelClassName: "p-0",
      children: <RecordPeek references={references} openRecord={openRecord} goBack={goBack} />,
    }],
  } : null, [recordSupportKey, references, openRecord, goBack, t]);
  useChatterContent(content);
  const preview = React.useMemo(() => recordSupportKey && references.length
    ? <RecordPeek references={references} openRecord={openRecord} goBack={goBack} close={close} />
    : null, [recordSupportKey, references, openRecord, goBack, close]);
  React.useEffect(() => {
    if (!recordSupportKey) return;
    setRecordPreview(owner, recordSupportKey, preview);
    return () => setRecordPreview(owner, recordSupportKey, null);
  }, [owner, preview, recordSupportKey, setRecordPreview]);
  React.useEffect(() => {
    if (preview) setCollapsed(false);
  }, [preview, setCollapsed]);
  return openSource;
}

function RecordPeek({ references, openRecord, goBack, close }: {
  references: readonly RecordPeekReference[];
  openRecord: (reference: RecordPeekReference) => void;
  goBack: (index: number) => void;
  close?: () => void;
}): React.ReactElement | null {
  const t = useUiT();
  const recordHref = useResourceRecordHrefLookup();
  const reference = references.at(-1);
  const registeredForm = useRegisteredForm(reference?.model ?? "");
  const RecordForm = registeredForm ? RegisteredFormView : FormView;
  const context = React.useMemo(() => reference ? { reference, openRecord } : null, [reference, openRecord]);
  if (!reference || !context) return null;
  const baseHref = recordHref(reference.model, reference.id);
  const href = baseHref ? recordTargetHref(baseHref, { tab: reference.tab, search: reference.search }) : undefined;
  return <RecordPeekContext.Provider value={context}>
    <ControlBandProvider host={undefined}>
      <div className={close ? "flex h-full min-h-0 flex-col" : undefined}>
        <nav aria-label={t("chatter.recordTrail")} className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border-subtle p-3 text-sm">
          {references.slice(0, -1).map((record, index) => <React.Fragment key={`${record.model}:${record.id}:${index}`}>
            <Button variant="ghost" size="sm" onClick={() => goBack(index)}>
              {record.label || t("chatter.previousRecord", { number: index + 1 })}
            </Button>
            <Glyph name="chevron-right" />
          </React.Fragment>)}
          {href ? <TextLink href={href} target="_blank" className="ml-auto">{t("chatter.openRecord")}</TextLink> : null}
          {close ? <Button variant="ghost" size="sm" onClick={close} className="ml-auto" aria-label={t("dialog.close")}><Glyph name="x" /></Button> : null}
        </nav>
        <div className={close ? "min-h-0 flex-1" : undefined}>
          <RecordForm key={`${reference.model}:${reference.id}:${reference.tab ?? ""}`} resource={reference.model} id={reference.id} readOnly hideRecordChrome recordPresentation="workspace"
            defaultRecordTab={reference.tab ?? undefined}
            className={close ? "h-full min-h-0" : "min-h-96"} />
        </div>
      </div>
    </ControlBandProvider>
  </RecordPeekContext.Provider>;
}

function sameRecordPeekReference(left: RecordPeekReference, right: RecordPeekReference): boolean {
  if (left.model !== right.model || left.id !== right.id || left.tab !== right.tab || left.page !== right.page) return false;
  const leftEntries = Object.entries(left.search ?? {});
  const rightEntries = Object.entries(right.search ?? {});
  return leftEntries.length === rightEntries.length
    && leftEntries.every(([key, value]) => right.search?.[key] === value);
}
