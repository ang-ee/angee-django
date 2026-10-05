import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useRecordChromeContextMaybe } from "../resource/record-chrome-context";
import { Button } from "../../ui/button";
import type { Tone } from "../../lib/tones";
import { Badge } from "../../ui/badge";

export interface RecordFieldMark {
  field: string;
  label: string;
  tone: Tone;
  onReveal: () => void;
}
export interface ActiveRecordForm {
  model: string;
  id: string;
  focusField: (field: string) => void;
  fieldLabel?: (field: string) => ReactNode;
}
interface MarkPublication {
  model: string;
  id: string;
  marks: readonly RecordFieldMark[];
}
type RevealedField = { model: string; id: string; field: string };
const Bridge = createContext<{
  revealed: RevealedField | null;
  setRevealed: (field: RevealedField) => void;
  marks: readonly MarkPublication[];
  publish: (owner: symbol, marks: readonly MarkPublication[] | null) => void;
  form: ActiveRecordForm | null;
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
  const value = useMemo(() => ({ marks, publish, form, setForm, revealed, setRevealed }), [marks, form, revealed]);
  return <Bridge.Provider value={value}>{children}</Bridge.Provider>;
}

export function useRecordFieldMarks(publications: readonly MarkPublication[]) {
  const bridge = useContext(Bridge);
  const publish = bridge?.publish;
  const owner = useRef(Symbol("record field marks"));
  useEffect(() => {
    publish?.(owner.current, publications);
    return () => publish?.(owner.current, null);
  }, [publish, publications]);
}

export function useRevealedRecordField() { return useContext(Bridge)?.revealed; }

export function useActiveRecordForm() { return useContext(Bridge)?.form ?? null; }

export function usePublishActiveRecordForm(form: ActiveRecordForm | null) {
  const setForm = useContext(Bridge)?.setForm;
  const owner = useRef(Symbol("record form"));
  useEffect(() => {
    if (!form) return;
    setForm?.(owner.current, form);
    return () => setForm?.(owner.current, null);
  }, [setForm, form]);
}

export function useRecordFieldMark(field: string) {
  const bridge = useContext(Bridge);
  const record = useRecordChromeContextMaybe();
  const publication = bridge?.marks.find((item) => item.id === record?.recordId
    && item.model.toLowerCase() === record.canonicalResource.toLowerCase());
  const mark = publication?.marks.find((item) => item.field === field);
  const reveal = useCallback(() => {
    if (mark && publication) {
      bridge?.setRevealed({ model: publication.model, id: publication.id, field });
      mark.onReveal();
    }
  }, [bridge?.setRevealed, field, mark, publication]);
  return mark ? { ...mark, reveal } : null;
}

/** Every native form field placement uses the same reveal control. */
export function RecordFieldMarkButton({ field, label }: { field: string; label?: ReactNode }) {
  const mark = useRecordFieldMark(field);
  if (!mark) return null;
  return <Button type="button" variant="ghost" size="sm" className="h-auto px-1 py-0" onClick={(event) => {
    event.preventDefault(); mark.reveal();
  }} aria-label={`${mark.label}: ${typeof label === "string" ? label : field}`}>
    <Badge tone={mark.tone} density="compact">{mark.label}</Badge>
  </Button>;
}
