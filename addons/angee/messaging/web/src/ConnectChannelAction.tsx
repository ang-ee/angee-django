import {
  useAuthoredMutation,
  type AuthoredDocument,
  type AuthoredVariables,
  type DocumentData,
} from "@angee/refine";
import {
  Button,
  Glyph,
  MutationDialog,
  type MutationDialogField,
  type MutationDialogParseValues,
  type MutationDialogValues,
} from "@angee/ui";
import * as React from "react";

import { CHANNEL_MODEL } from "./documents";
import { useMessagingT, type MessagingT } from "./i18n";
import { usePairingConnect } from "./usePairingConnect";

export type ConnectChannelFields = (
  t: MessagingT,
) => readonly MutationDialogField[];
export type ConnectChannelParseValues<TValues> = (
  values: Readonly<Record<string, unknown>>,
  t: MessagingT,
) => TValues;

interface ConnectChannelActionBaseProps<
  TDocument extends AuthoredDocument,
  TValues extends AuthoredVariables<TDocument>,
> {
  /** Vendor-authored create operation. */
  document: TDocument;
  /** Stable vendor declaration of the dialog fields. */
  fields: ConnectChannelFields;
  /** Vendor message range, for example `channel.matrix`. */
  i18nPrefix: string;
  /** Typed projection from raw dialog controls to the document's variables. */
  parseValues: ConnectChannelParseValues<TValues>;
  /** Optional raw field defaults owned by the vendor declaration. */
  initialValues?: MutationDialogValues;
  /** Strip dialog-only values before the authored document crosses the wire. */
  variables?: (values: TValues) => AuthoredVariables<TDocument>;
}

export interface MutationConnectChannelActionProps<
  TDocument extends AuthoredDocument,
  TValues extends AuthoredVariables<TDocument>,
> extends ConnectChannelActionBaseProps<TDocument, TValues> {
  kind: "mutation";
}

export interface PairingConnectChannelActionProps<
  TDocument extends AuthoredDocument,
  TResultField extends keyof DocumentData<TDocument>,
  TValues extends AuthoredVariables<TDocument>,
> extends ConnectChannelActionBaseProps<TDocument, TValues> {
  kind: "pairing";
  resultField: TResultField;
  instructionKey?: string;
  /** Optional content shown after pairing starts, derived from submitted values. */
  nextStep?: (values: TValues, t: MessagingT) => React.ReactNode;
}

export type ConnectChannelActionProps<
  TDocument extends AuthoredDocument,
  TResultField extends keyof DocumentData<TDocument>,
  TValues extends AuthoredVariables<TDocument>,
> =
  | MutationConnectChannelActionProps<TDocument, TValues>
  | PairingConnectChannelActionProps<TDocument, TResultField, TValues>;

/**
 * Canonical economy for messaging connect actions: this factory owns the button,
 * typed mutation dialog, generic name/submit/submitting copy, invalidation, and
 * optional vendor error fallback. Vendors declare only their operation, fields,
 * message range, value projection, and (for live bridges) the pairing hand-off.
 *
 * Vendor ranges are composed into the runtime by addon manifests. Providerless
 * unit renders intentionally expose unresolved vendor keys; duplicating every
 * vendor string in messaging core would defeat that composition boundary.
 */
export function ConnectChannelAction<
  TDocument extends AuthoredDocument,
  TResultField extends keyof DocumentData<TDocument>,
  TValues extends AuthoredVariables<TDocument>,
>(
  props: ConnectChannelActionProps<TDocument, TResultField, TValues>,
): React.ReactElement {
  switch (props.kind) {
    case "mutation":
      return <MutationConnectChannelAction {...props} />;
    case "pairing":
      return <PairingConnectChannelAction {...props} />;
  }
}

function MutationConnectChannelAction<
  TDocument extends AuthoredDocument,
  TValues extends AuthoredVariables<TDocument>,
>(
  props: MutationConnectChannelActionProps<TDocument, TValues>,
): React.ReactElement {
  const [connect] = useAuthoredMutation(props.document, {
    invalidateModels: [CHANNEL_MODEL],
  });
  return (
    <ConnectChannelDialog
      {...props}
      onSubmit={(values) =>
        connect(props.variables ? props.variables(values) : values)
      }
    />
  );
}

function PairingConnectChannelAction<
  TDocument extends AuthoredDocument,
  TResultField extends keyof DocumentData<TDocument>,
  TValues extends AuthoredVariables<TDocument>,
>({
  resultField,
  instructionKey,
  nextStep,
  variables,
  ...props
}: PairingConnectChannelActionProps<TDocument, TResultField, TValues>): React.ReactElement {
  const t = useMessagingT();
  const { connect, pairingDialog } = usePairingConnect(
    props.document,
    resultField,
    instructionKey ? t(instructionKey) : undefined,
  );
  return (
    <>
      <ConnectChannelDialog
        {...props}
        onSubmit={(values) =>
          connect(
            variables ? variables(values) : values,
            nextStep ? () => nextStep(values, t) : undefined,
          )
        }
      />
      {pairingDialog}
    </>
  );
}

interface ConnectChannelDialogProps<
  TDocument extends AuthoredDocument,
  TValues extends AuthoredVariables<TDocument>,
> extends ConnectChannelActionBaseProps<TDocument, TValues> {
  onSubmit: (
    values: TValues,
  ) => unknown | Promise<unknown>;
}

function ConnectChannelDialog<
  TDocument extends AuthoredDocument,
  TValues extends AuthoredVariables<TDocument>,
>({
  fields,
  i18nPrefix,
  initialValues,
  parseValues,
  onSubmit,
}: ConnectChannelDialogProps<TDocument, TValues>): React.ReactElement {
  const t = useMessagingT();
  const [open, setOpen] = React.useState(false);
  const dialogFields = React.useMemo(() => fields(t), [fields, t]);
  const typedParseValues = React.useCallback<MutationDialogParseValues<TValues>>(
    (values) => parseValues(values, t),
    [parseValues, t],
  );
  const errorFallback = optionalTranslation(t, `${i18nPrefix}.error`);

  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t(`${i18nPrefix}.button`)}
      </Button>
      <MutationDialog
        open={open}
        onOpenChange={setOpen}
        title={t(`${i18nPrefix}.title`)}
        description={t(`${i18nPrefix}.description`)}
        fields={dialogFields}
        initialValues={initialValues}
        submitLabel={t("channel.connect.submit")}
        submittingLabel={t("channel.connect.submitting")}
        errorFallback={errorFallback}
        parseValues={typedParseValues}
        onSubmit={onSubmit}
      />
    </>
  );
}

function optionalTranslation(t: MessagingT, key: string): string | undefined {
  const translated = t(key);
  return translated === key ? undefined : translated;
}
