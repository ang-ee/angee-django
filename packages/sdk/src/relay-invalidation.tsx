import {
  createElement,
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { Provider as UrqlProvider } from "urql";
import type { Client } from "@urql/core";

import { useAuthoredSubscription } from "./authored-hooks";
import { makeContext } from "./make-context";
import { createRefetchRegistry, type RefetchRegistry } from "./relay-registry";
import { typeNameForModel } from "./selection";

/**
 * The change subscription for one model: `subscription { <noun>Changed { … } }`,
 * the per-model event the schema publishes. Derived from the GraphQL typename so
 * it never drifts from the field naming (`Note` -> `noteChanged`).
 */
export function changeSubscriptionDocument(typename: string): string {
  const typeName = typeNameForModel(typename);
  const field = `${typeName.charAt(0).toLowerCase()}${typeName.slice(1)}Changed`;
  return (
    `subscription angee${typename}Changed { ` +
    `${field} { model id action changedFields changedValues } }`
  );
}

const RegistryContext = makeContext<RefetchRegistry>("RelayInvalidationProvider");

/** Subscribe to one model's change event and invalidate it on every push. */
function ModelChangeListener(props: {
  typename: string;
  registry: RefetchRegistry;
}): ReactNode {
  const { typename, registry } = props;
  const document = useMemo(() => changeSubscriptionDocument(typename), [typename]);
  useAuthoredSubscription(document, undefined, {
    onData: () => registry.invalidate([typename]),
  });
  return null;
}

/** Open one change subscription per model in view, on the given client. */
function LiveInvalidation(props: {
  registry: RefetchRegistry;
  client: Client;
}): ReactNode {
  const { registry, client } = props;
  const typenames = useSyncExternalStore(
    registry.subscribe,
    registry.typenames,
    registry.typenames,
  );
  return createElement(
    UrqlProvider,
    { value: client },
    typenames.map((typename) =>
      createElement(ModelChangeListener, { key: typename, typename, registry }),
    ),
  );
}

/**
 * Provide the refetch registry. When `client` is given — the endpoint that
 * carries the change subscriptions, i.e. the console schema — and `autoSubscribe`
 * is on (the default), also open one `<noun>Changed` subscription per model in
 * view, so cross-actor writes and deletes refetch the right queries. Without a
 * client the registry still works for post-write invalidation.
 */
export function RelayInvalidationProvider(props: {
  client?: Client;
  autoSubscribe?: boolean;
  children: ReactNode;
}): ReactNode {
  const [registry] = useState(createRefetchRegistry);
  const autoSubscribe = props.autoSubscribe ?? true;
  const client = props.client;
  return RegistryContext.Provider({
    value: registry,
    children: createElement(
      Fragment,
      null,
      autoSubscribe && client
        ? createElement(LiveInvalidation, { registry, client })
        : null,
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
