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

export interface ConfirmOptions {
  title: ReactNode;
  body?: ReactNode;
  confirm?: ReactNode;
  cancel?: ReactNode;
  danger?: boolean;
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

let nextConfirmId = 1;

const ConfirmContext = createContext<ConfirmContextValue | null>(null);

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

  const context = useMemo<ConfirmContextValue>(() => ({ confirm }), [confirm]);

  return (
    <ConfirmContext.Provider value={context}>
      {children}
      <ConfirmDialog request={active} onResolve={resolveActive} />
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
        <AlertDialog.Content intent={options.danger ? "danger" : "default"}>
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
              intent={options.danger ? "danger" : "default"}
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
