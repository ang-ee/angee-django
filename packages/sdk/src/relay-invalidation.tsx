import { createElement, useEffect, useMemo, useRef, type ReactNode } from "react";

import { useAuthoredSubscription } from "./authored-hooks";
import { makeContext } from "./make-context";
import { createRefetchRegistry, type RefetchRegistry } from "./relay-registry";
import { typeNameForModel } from "./selection";

/** The single change firehose: one stream of every change the actor may see. */
export const CHANGE_EVENTS_DOCUMENT =
  "subscription angeeEvents { events { model id action changedFields computedFieldsChanged } }";

interface ChangeEvent {
  model: string;
  id: string;
  action: string;
  changedFields: readonly string[];
  computedFieldsChanged: readonly string[];
}

const RegistryContext = makeContext<RefetchRegistry>("RelayInvalidationProvider");

/** Drives the registry from the change firehose; mounted only when subscribing. */
function ChangeEventListener(props: { registry: RefetchRegistry }): ReactNode {
  const { registry } = props;
  useAuthoredSubscription<{ events: ChangeEvent }>(
    CHANGE_EVENTS_DOCUMENT,
    undefined,
    {
      onData: (data) => {
        const model = data.events?.model;
        if (model) registry.invalidate([typeNameForModel(model)]);
      },
    },
  );
  return null;
}

/**
 * Provide the refetch registry and, by default, subscribe to the change
 * firehose so cross-actor writes and deletes refetch the right queries. Pass
 * `autoSubscribe={false}` to use the registry without the live stream.
 */
export function RelayInvalidationProvider(props: {
  autoSubscribe?: boolean;
  children: ReactNode;
}): ReactNode {
  const registryRef = useRef<RefetchRegistry>(undefined as unknown as RefetchRegistry);
  registryRef.current ??= createRefetchRegistry();
  const registry = registryRef.current;
  const autoSubscribe = props.autoSubscribe ?? true;
  return RegistryContext.Provider({
    value: registry,
    children: createElement(
      "div",
      { style: { display: "contents" } },
      autoSubscribe ? createElement(ChangeEventListener, { registry }) : null,
      props.children,
    ),
  });
}

function useRegistry(): RefetchRegistry | null {
  return RegistryContext.useMaybe();
}

/** Register a query's refetch under its model typename for the registry's life. */
export function useRegisterModelRefetch(
  modelLabel: string,
  refetch: () => void,
  enabled: boolean,
): void {
  const registry = useRegistry();
  const refetchRef = useRef(refetch);
  refetchRef.current = refetch;
  useEffect(() => {
    if (!registry || !enabled || !modelLabel) return;
    return registry.register(typeNameForModel(modelLabel), () =>
      refetchRef.current(),
    );
  }, [registry, enabled, modelLabel]);
}

/** An imperative invalidator for one model — the delete-path companion. */
export function useModelInvalidation(
  modelLabel: string | null | undefined,
): () => void {
  const registry = useRegistry();
  return useMemo(
    () => () => {
      if (registry && modelLabel) registry.invalidate([typeNameForModel(modelLabel)]);
    },
    [registry, modelLabel],
  );
}

/** An imperative invalidator for several models at once. */
export function useInvalidateModels(): (modelLabels: readonly string[]) => void {
  const registry = useRegistry();
  return useMemo(
    () => (modelLabels: readonly string[]) => {
      if (registry) registry.invalidate(modelLabels.map(typeNameForModel));
    },
    [registry],
  );
}
