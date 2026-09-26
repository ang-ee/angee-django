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

export interface RecordPeekOpenOptions {
  /** Initial asynchronous content may select Records only before explicit tab intent. */
  tabActivation?: "explicit" | "initial";
}

export type RecordPeekOpen = (
  reference: RecordPeekReference,
  options?: RecordPeekOpenOptions,
) => void;

interface RecordPeekContextValue {
  reference: RecordPeekReference;
  openRecord: RecordPeekOpen;
}

const RecordPeekContext = React.createContext<RecordPeekContextValue | null>(null);

/** Record-owned sections can follow sources in the same peek breadcrumb. */
export function useRecordPeekContext(): RecordPeekContextValue | null {
  return React.useContext(RecordPeekContext);
}

/** Publish a readonly native record form into Chatter. */
export function useRecordPeek(): RecordPeekOpen {
  const t = useUiT();
  const { setActiveTab, setCollapsed, setInitialActiveTab } = useChatter();
  const [references, setReferences] = React.useState<readonly RecordPeekReference[]>([]);
  const openRecord = React.useCallback((
    reference: RecordPeekReference,
    options?: RecordPeekOpenOptions,
  ) => {
    setReferences((current) => {
      const previous = current.at(-1);
      if (previous && sameRecordPeekReference(previous, reference)) return current;
      return [...current, reference];
    });
    if (options?.tabActivation === "initial") setInitialActiveTab("records");
    else setActiveTab("records");
    setCollapsed(false);
  }, [setActiveTab, setCollapsed, setInitialActiveTab]);
  const goBack = React.useCallback((index: number) => {
    setReferences((current) => current.slice(0, index + 1));
  }, []);
  const content = React.useMemo(() => references.length ? {
    tabs: [{
      id: "records",
      label: t("chatter.tabRecords"),
      icon: "file",
      panelClassName: "p-0",
      children: <RecordPeek references={references} openRecord={openRecord} goBack={goBack} />,
    }],
  } : null, [references, openRecord, goBack, t]);
  useChatterContent(content);
  return openRecord;
}

function RecordPeek({ references, openRecord, goBack }: {
  references: readonly RecordPeekReference[];
  openRecord: RecordPeekOpen;
  goBack: (index: number) => void;
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
      <div>
        <nav aria-label={t("chatter.recordTrail")} className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border-subtle p-3 text-sm">
          {references.slice(0, -1).map((record, index) => <React.Fragment key={`${record.model}:${record.id}:${index}`}>
            <Button variant="ghost" size="sm" onClick={() => goBack(index)}>
              {record.label || t("chatter.previousRecord", { number: index + 1 })}
            </Button>
            <Glyph name="chevron-right" />
          </React.Fragment>)}
          {href ? <TextLink href={href} target="_blank" className="ml-auto">{t("chatter.openRecord")}</TextLink> : null}
        </nav>
        <div>
          <RecordForm key={`${reference.model}:${reference.id}:${reference.tab ?? ""}`} resource={reference.model} id={reference.id} readOnly hideRecordChrome recordPresentation="workspace"
            defaultRecordTab={reference.tab ?? undefined}
            className="min-h-96" />
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
