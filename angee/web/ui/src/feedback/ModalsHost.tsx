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

import { useUiT } from "../i18n";
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
  // Left undefined when the caller gave no label; ConfirmDialog applies the
  // translated default at render (the hook can't run in this helper).
  confirm?: ReactNode;
  cancel?: ReactNode;
  danger: boolean;
}

interface ConfirmContextValue {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
}

interface PromptContextValue {
  prompt: (options: PromptOptions) => Promise<Record<string, string> | null>;
}

/** A queued modal request: its options and the resolver for its result promise. */
interface QueuedRequest<TOptions, TResult> {
  id: number;
  options: TOptions;
  resolve: (result: TResult) => void;
}

interface QueuedDialog<TOptions, TResult> {
  active: QueuedRequest<TOptions, TResult> | null;
  enqueue: (options: TOptions) => Promise<TResult>;
  resolveActive: (result: TResult) => void;
}

let nextQueuedId = 1;

/**
 * A FIFO queue of modal requests — the one owner of the confirm/prompt
 * request-queue plumbing. Holds the pending list, exposes the head as `active`,
 * and `enqueue` returns a promise that `resolveActive` settles (dropping the
 * head). `enqueue`/`resolveActive` are stable; `active` updates as it drains.
 */
function useQueuedDialog<TOptions, TResult>(): QueuedDialog<TOptions, TResult> {
  const [requests, setRequests] = useState<QueuedRequest<TOptions, TResult>[]>([]);
  const active = requests[0] ?? null;
  const activeRef = useRef(active);
  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  const enqueue = useCallback((options: TOptions) => {
    return new Promise<TResult>((resolve) => {
      const request: QueuedRequest<TOptions, TResult> = {
        id: nextQueuedId,
        options,
        resolve,
      };
      nextQueuedId += 1;
      setRequests((current) => [...current, request]);
    });
  }, []);

  const resolveActive = useCallback((result: TResult) => {
    const request = activeRef.current;
    if (!request) return;
    request.resolve(result);
    setRequests((current) => current.filter((item) => item.id !== request.id));
  }, []);

  return { active, enqueue, resolveActive };
}

const ConfirmContext = createContext<ConfirmContextValue | null>(null);
const PromptContext = createContext<PromptContextValue | null>(null);

export function ModalsHost({
  children,
}: {
  children?: ReactNode;
}): ReactElement {
  const confirmQueue = useQueuedDialog<NormalisedConfirmOptions, boolean>();
  const promptQueue = useQueuedDialog<
    PromptOptions,
    Record<string, string> | null
  >();

  const confirm = useCallback(
    (options: ConfirmOptions) =>
      confirmQueue.enqueue(normaliseConfirmOptions(options)),
    [confirmQueue.enqueue],
  );
  const context = useMemo<ConfirmContextValue>(() => ({ confirm }), [confirm]);
  const promptContext = useMemo<PromptContextValue>(
    () => ({ prompt: promptQueue.enqueue }),
    [promptQueue.enqueue],
  );

  return (
    <ConfirmContext.Provider value={context}>
      <PromptContext.Provider value={promptContext}>
        {children}
        <ConfirmDialog
          request={confirmQueue.active}
          onResolve={confirmQueue.resolveActive}
        />
        <PromptDialog
          request={promptQueue.active}
          onResolve={promptQueue.resolveActive}
        />
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
  request: QueuedRequest<NormalisedConfirmOptions, boolean> | null;
  onResolve: (confirmed: boolean) => void;
}): ReactElement | null {
  const t = useUiT();
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
              {options.cancel ?? t("modal.cancel")}
            </AlertDialog.Cancel>
            <AlertDialog.Action
              type="button"
              tone={options.danger ? "danger" : "info"}
              onClick={() => onResolve(true)}
            >
              {options.confirm ?? t("modal.confirm")}
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
    ...(options.confirm !== undefined ? { confirm: options.confirm } : {}),
    ...(options.cancel !== undefined ? { cancel: options.cancel } : {}),
    danger: options.danger ?? false,
  };
}

function PromptDialog({
  request,
  onResolve,
}: {
  request: QueuedRequest<PromptOptions, Record<string, string> | null> | null;
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
  request: QueuedRequest<PromptOptions, Record<string, string> | null>;
  onResolve: (values: Record<string, string> | null) => void;
}): ReactElement {
  const t = useUiT();
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
                    onChange={(event) => {
                      const next = event.currentTarget.value;
                      setValues((current) => ({
                        ...current,
                        [field.name]: next,
                      }));
                    }}
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
                {options.cancel ?? t("modal.cancel")}
              </AlertDialog.Cancel>
            )}
            <AlertDialog.Action type="button" tone="info" onClick={submit}>
              {options.confirm ?? (readOnly ? t("modal.done") : t("modal.confirm"))}
            </AlertDialog.Action>
          </AlertDialog.Footer>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
