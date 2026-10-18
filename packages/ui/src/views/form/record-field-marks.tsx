import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRecordChromeContextMaybe } from "../resource/record-chrome-context";
import { Button } from "../../ui/button";
import type { Tone } from "../../lib/tones";
import { Badge } from "../../ui/badge";
import { Tooltip } from "../../ui/tooltip";
import { RecordReference } from "../relation/RecordReference";
import { Glyph } from "../../chrome/Glyph";
import { useUiT } from "../../i18n";

export interface RecordFieldMark {
  field: string;
  label: string;
  tone: Tone;
  /** Hover copy naming the source that supplied this field value. */
  origin?: string;
  /** A saved record the mark opens, such as the applied statement. */
  target?: { model: string; id: string };
  icon?: string;
  /** An owner-supplied reveal action, such as opening a decision pane. */
  onReveal?: () => void;
}
export interface ActiveRecordForm {
  model: string;
  id: string;
  focusField: (field: string) => void;
}
interface MarkPublication {
  model: string;
  id: string;
  marks: readonly RecordFieldMark[];
}
type RevealedField = { model: string; id: string; field: string };
const State = createContext<{
  revealed: RevealedField | null;
  marks: readonly MarkPublication[];
  form: ActiveRecordForm | null;
} | null>(null);
const Dispatch = createContext<{
  setRevealed: (field: RevealedField | null) => void;
  publish: (owner: symbol, marks: readonly MarkPublication[] | null) => void;
  setForm: (owner: symbol, form: ActiveRecordForm | null) => void;
} | null>(null);

/** Shared by the form and its pane; carries presentation only, never record values. */
export function RecordFieldMarksProvider({ children }: { children: ReactNode }) {
  const [revealed, setRevealed] = useState<RevealedField | null>(null);
  const [publications, setPublications] = useState(new Map<symbol, readonly MarkPublication[]>());
  const [forms, setForms] = useState(new Map<symbol, ActiveRecordForm>());
  const publish = useCallback((owner: symbol, marks: readonly MarkPublication[] | null) => {
    setPublications((previous) => { const next = new Map(previous); if (marks) next.set(owner, marks); else next.delete(owner); return next; });
  }, []);
  const setForm = useCallback((owner: symbol, form: ActiveRecordForm | null) => {
    setForms((previous) => { const next = new Map(previous); if (form) next.set(owner, form); else next.delete(owner); return next; });
  }, []);
  const marks = useMemo(() => [...publications.values()].flat(), [publications]);
  const form = [...forms.values()].at(-1) ?? null;
  const state = useMemo(() => ({ marks, form, revealed }), [marks, form, revealed]);
  const dispatch = useMemo(() => ({ publish, setForm, setRevealed }), [publish, setForm]);
  useEffect(() => {
    if (revealed && !marks.some((item) => item.model === revealed.model && item.id === revealed.id
      && item.marks.some((mark) => mark.field === revealed.field))) setRevealed(null);
  }, [marks, revealed]);
  return <Dispatch.Provider value={dispatch}><State.Provider value={state}>{children}</State.Provider></Dispatch.Provider>;
}

export function useRecordFieldMarks(publications: readonly MarkPublication[]) {
  const bridge = useContext(Dispatch);
  const publish = bridge?.publish;
  const owner = useRef(Symbol("record field marks"));
  useEffect(() => {
    publish?.(owner.current, publications);
    return () => publish?.(owner.current, null);
  }, [publish, publications]);
}

export function useRevealedRecordField() { return useContext(State)?.revealed; }

export function useActiveRecordForm() { return useContext(State)?.form ?? null; }

export function usePublishActiveRecordForm(form: ActiveRecordForm | null) {
  const setForm = useContext(Dispatch)?.setForm;
  const owner = useRef(Symbol("record form"));
  useEffect(() => {
    if (!form) return;
    setForm?.(owner.current, form);
    return () => setForm?.(owner.current, null);
  }, [setForm, form]);
}

export function useRecordFieldMarksForField(field: string) {
  const bridge = useContext(State);
  const setRevealed = useContext(Dispatch)?.setRevealed;
  const record = useRecordChromeContextMaybe();
  return useMemo(() => (bridge?.marks.flatMap((publication) => {
    if (publication.id !== record?.recordId
      || !record
      || publication.model.toLowerCase() !== record.canonicalResource.toLowerCase()) return [];
    return publication.marks.filter((mark) => mark.field === field).map((mark) => ({
      ...mark,
      reveal: mark.onReveal ? () => {
        setRevealed?.({ model: publication.model, id: publication.id, field });
        mark.onReveal?.();
      } : undefined,
    }));
  }) ?? []), [bridge?.marks, field, record, setRevealed]);
}

/** Every native form field placement composes attention, provenance, and access marks once. */
export function RecordFieldMarkControl({ field, label, restricted = false }: {
  field: string;
  label?: ReactNode;
  restricted?: boolean;
}) {
  const t = useUiT();
  const published = useRecordFieldMarksForField(field);
  const marks: readonly RecordFieldMark[] = restricted
    ? [...published, { field, label: t("form.restricted"), tone: "neutral", icon: "lock" }]
    : published;
  if (!marks.length) return null;
  return <>
    {marks.map((mark, index) => (
      <RecordFieldMarkItem key={`${mark.label}:${index}`} mark={mark} field={field} label={label} />
    ))}
  </>;
}

function RecordFieldMarkItem({ mark, field, label }: {
  mark: RecordFieldMark & { reveal?: () => void };
  field: string;
  label?: ReactNode;
}) {
  const ariaLabel = `${mark.label}: ${typeof label === "string" ? label : field}`;
  const badge = <Badge tone={mark.tone} density="compact">
    {mark.icon ? <Glyph decorative name={mark.icon} /> : null}{mark.label}
  </Badge>;
  if (mark.target) {
    return <Tooltip label={mark.origin}>
      <span className="inline-flex"><RecordReference {...mark.target} label={badge} /></span>
    </Tooltip>;
  }
  if (!mark.reveal) return <span aria-label={ariaLabel}>{badge}</span>;
  return <Button type="button" variant="ghost" size="sm" className="h-auto px-1 py-0"
    onClick={mark.reveal} aria-label={ariaLabel}>{badge}</Button>;
}
