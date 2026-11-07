import {
  useAuthoredMutation,
  type AuthoredDocument,
  type AuthoredMutate,
  type DocumentData,
} from "@angee/refine";
import * as React from "react";

import { CHANNEL_MODEL } from "./documents";
import { PairingDialog } from "./PairingDialog";

type PairingMutationResult = { id?: unknown } | null | undefined;
type PairingTarget = {
  channelId: string;
  nextStep?: React.ReactNode;
};
type PairingNextStep = () => React.ReactNode;

/**
 * Run one channel-create mutation and open shared pairing for its returned id.
 */
export function usePairingConnect<
  TDocument extends AuthoredDocument,
  TResultField extends keyof DocumentData<TDocument>,
>(
  mutation: TDocument,
  resultField: TResultField,
  instruction = "",
) {
  const [pairingTarget, setPairingTarget] =
    React.useState<PairingTarget | null>(null);
  const [mutate, connectState] = useAuthoredMutation(mutation, {
    invalidateModels: [CHANNEL_MODEL],
  });
  const connect = React.useCallback(
    async (
      variables?: Parameters<AuthoredMutate<TDocument>>[0],
      nextStep?: PairingNextStep,
    ) => {
      const data = await mutate(variables);
      const result = data?.[resultField] as PairingMutationResult;
      const id = result?.id;
      if (id) {
        setPairingTarget({
          channelId: String(id),
          ...(nextStep ? { nextStep: nextStep() } : {}),
        });
      }
      return data;
    },
    [mutate, resultField],
  );
  const pairingDialog = (
    <PairingDialog
      channelId={pairingTarget?.channelId ?? null}
      instruction={instruction}
      nextStep={pairingTarget?.nextStep}
      onClose={() => setPairingTarget(null)}
    />
  );
  return { connect, connectState, pairingDialog };
}
