import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { AlertDialog } from "../ui/alert-dialog";
import { Input } from "../ui/input";

export interface ConfirmOptions {
  title: ReactNode;
  body?: ReactNode;
  confirm?: ReactNode;
  cancel?: ReactNode;
  danger?: boolean;
}

/** One labeled input collected (or revealed read-only) by a {@link usePrompt} dialog. */
export interface PromptField {
  name: string;
  label?: ReactNode;
  type?: "text" | "password";
  placeholder?: string;
  defaultValue?: string;
  /** Show the value uneditable — e.g. revealing a freshly rotated secret. */
  readOnly?: boolean;
}

export interface PromptOptions {
  title: ReactNode;
  body?: ReactNode;
  fields: readonly PromptField[];
  confirm?: ReactNode;
  cancel?: ReactNode;
}

interface NormalisedConfirmOptions {
  title: ReactNode;
  body?: ReactNode;
  confirm: ReactNode;
  cancel: ReactNode;
  danger: boolean;
}

interface ConfirmRequest {
  id: number;
  options: NormalisedConfirmOptions;
  resolve: (confirmed: boolean) => void;
}

interface ConfirmContextValue {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
}

interface PromptRequest {
  id: number;
  options: PromptOptions;
  resolve: (values: Record<string, string> | null) => void;
}

interface PromptContextValue {
  prompt: (options: PromptOptions) => Promise<Record<string, string> | null>;
}

let nextConfirmId = 1;
let nextPromptId = 1;

const ConfirmContext = createContext<ConfirmContextValue | null>(null);
const PromptContext = createContext<PromptContextValue | null>(null);

export function ModalsHost({
  children,
}: {
  children?: ReactNode;
}): ReactElement {
  const [requests, setRequests] = useState<ConfirmRequest[]>([]);
  const active = requests[0] ?? null;
  const activeRef = useRef<ConfirmRequest | null>(active);

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  const confirm = useCallback((options: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      const request: ConfirmRequest = {
        id: nextConfirmId,
        options: normaliseConfirmOptions(options),
        resolve,
      };
      nextConfirmId += 1;
      setRequests((current) => [...current, request]);
    });
  }, []);

  const resolveActive = useCallback((confirmed: boolean) => {
    const request = activeRef.current;
    if (!request) return;
    request.resolve(confirmed);
    setRequests((current) =>
      current.filter((item) => item.id !== request.id),
    );
  }, []);

  const [promptRequests, setPromptRequests] = useState<PromptRequest[]>([]);
  const activePrompt = promptRequests[0] ?? null;
  const activePromptRef = useRef<PromptRequest | null>(activePrompt);

  useEffect(() => {
    activePromptRef.current = activePrompt;
  }, [activePrompt]);

  const prompt = useCallback((options: PromptOptions) => {
    return new Promise<Record<string, string> | null>((resolve) => {
      const request: PromptRequest = { id: nextPromptId, options, resolve };
      nextPromptId += 1;
      setPromptRequests((current) => [...current, request]);
    });
  }, []);

  const resolveActivePrompt = useCallback(
    (values: Record<string, string> | null) => {
      const request = activePromptRef.current;
      if (!request) return;
      request.resolve(values);
      setPromptRequests((current) =>
        current.filter((item) => item.id !== request.id),
      );
    },
    [],
  );

  const context = useMemo<ConfirmContextValue>(() => ({ confirm }), [confirm]);
  const promptContext = useMemo<PromptContextValue>(
    () => ({ prompt }),
    [prompt],
  );

  return (
    <ConfirmContext.Provider value={context}>
      <PromptContext.Provider value={promptContext}>
        {children}
        <ConfirmDialog request={active} onResolve={resolveActive} />
        <PromptDialog request={activePrompt} onResolve={resolveActivePrompt} />
      </PromptContext.Provider>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): (options: ConfirmOptions) => Promise<boolean> {
  const context = useContext(ConfirmContext);
  if (!context) {
    throw new Error("useConfirm must be used under ModalsHost.");
  }
  return context.confirm;
}

export function usePrompt(): (
  options: PromptOptions,
) => Promise<Record<string, string> | null> {
  const context = useContext(PromptContext);
  if (!context) {
    throw new Error("usePrompt must be used under ModalsHost.");
  }
  return context.prompt;
}

function ConfirmDialog({
  request,
  onResolve,
}: {
  request: ConfirmRequest | null;
  onResolve: (confirmed: boolean) => void;
}): ReactElement | null {
  if (!request) return null;
  const { options } = request;
  return (
    <AlertDialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onResolve(false);
      }}
    >
      <AlertDialog.Portal>
        <AlertDialog.Backdrop />
        <AlertDialog.Content tone={options.danger ? "danger" : "info"}>
          <AlertDialog.Body className="space-y-3 p-5">
            <AlertDialog.Title>{options.title}</AlertDialog.Title>
            {options.body ? (
              <AlertDialog.Description>{options.body}</AlertDialog.Description>
            ) : null}
          </AlertDialog.Body>
          <AlertDialog.Footer>
            <AlertDialog.Cancel type="button" onClick={() => onResolve(false)}>
              {options.cancel}
            </AlertDialog.Cancel>
            <AlertDialog.Action
              type="button"
              tone={options.danger ? "danger" : "info"}
              onClick={() => onResolve(true)}
            >
              {options.confirm}
            </AlertDialog.Action>
          </AlertDialog.Footer>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

function normaliseConfirmOptions(
  options: ConfirmOptions,
): NormalisedConfirmOptions {
  return {
    title: options.title,
    ...(options.body !== undefined ? { body: options.body } : {}),
    confirm: options.confirm ?? "Confirm",
    cancel: options.cancel ?? "Cancel",
    danger: options.danger ?? false,
  };
}

function PromptDialog({
  request,
  onResolve,
}: {
  request: PromptRequest | null;
  onResolve: (values: Record<string, string> | null) => void;
}): ReactElement | null {
  if (!request) return null;
  // Key by request id so input state resets for each new prompt.
  return <PromptDialogForm key={request.id} request={request} onResolve={onResolve} />;
}

function PromptDialogForm({
  request,
  onResolve,
}: {
  request: PromptRequest;
  onResolve: (values: Record<string, string> | null) => void;
}): ReactElement {
  const { options } = request;
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      options.fields.map((field) => [field.name, field.defaultValue ?? ""]),
    ),
  );
  const readOnly = options.fields.every((field) => field.readOnly);
  // Resolve directly from the Action click (mirrors ConfirmDialog): base-ui's
  // Action also fires onOpenChange(false) → onResolve(null), but the promise is
  // already settled with the values, so the trailing null is a no-op.
  const submit = () => onResolve(values);

  return (
    <AlertDialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onResolve(null);
      }}
    >
      <AlertDialog.Portal>
        <AlertDialog.Backdrop />
        <AlertDialog.Content tone="info">
          <AlertDialog.Body className="space-y-3 p-5">
            <AlertDialog.Title>{options.title}</AlertDialog.Title>
            {options.body ? (
              <AlertDialog.Description>{options.body}</AlertDialog.Description>
            ) : null}
            <div className="grid gap-3">
              {options.fields.map((field, index) => (
                <label key={field.name} className="grid gap-1">
                  {field.label ? (
                    <span className="text-xs font-medium uppercase tracking-wide text-fg-muted">
                      {field.label}
                    </span>
                  ) : null}
                  <Input
                    type={field.type ?? "text"}
                    value={values[field.name] ?? ""}
                    placeholder={field.placeholder}
                    readOnly={field.readOnly}
                    autoFocus={index === 0 && !field.readOnly}
                    aria-label={
                      typeof field.label === "string" ? field.label : field.name
                    }
                    onChange={(event) =>
                      setValues((current) => ({
                        ...current,
                        [field.name]: event.currentTarget.value,
                      }))
                    }
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !readOnly) {
                        event.preventDefault();
                        submit();
                      }
                    }}
                  />
                </label>
              ))}
            </div>
          </AlertDialog.Body>
          <AlertDialog.Footer>
            {readOnly ? null : (
              <AlertDialog.Cancel type="button" onClick={() => onResolve(null)}>
                {options.cancel ?? "Cancel"}
              </AlertDialog.Cancel>
            )}
            <AlertDialog.Action type="button" tone="info" onClick={submit}>
              {options.confirm ?? (readOnly ? "Done" : "Confirm")}
            </AlertDialog.Action>
          </AlertDialog.Footer>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
