import type { AuthoredDocument, AuthoredVariables } from "@angee/refine";
import {
  ActionTrigger,
  MutationDialog,
  useAuthoredResourceMutation,
  type DescriptorField,
  type MutationDialogParseValues,
} from "@angee/ui";
import * as React from "react";

import { usePartiesT, type PartiesT } from "./i18n";
import { DIRECTORY_MODEL } from "./resources";

/** A vendor's connect dialog fields, labelled through the parties namespace its messages join. */
export type ConnectDirectoryFields = (t: PartiesT) => readonly DescriptorField[];

export interface ConnectDirectoryActionProps<
  TDocument extends AuthoredDocument,
  TValues extends AuthoredVariables<TDocument>,
> {
  /** The vendor's connect mutation: it runs `Directory.objects.connect` and returns the directory. */
  document: TDocument;
  /** Stable vendor declaration of the dialog fields; the directory's name is `directory.name`. */
  fields: ConnectDirectoryFields;
  /** The vendor's message range in the parties namespace, for example `directory.hubspot`. */
  i18nPrefix: string;
  /** Stable projection from the raw dialog controls to the document's variables. */
  parseValues: MutationDialogParseValues<TValues>;
}

/**
 * The one connect ceremony of a contacts directory, which each directory vendor
 * composes into the directories toolbar (`DIRECTORIES_TOOLBAR`). It owns the menu
 * trigger, the typed mutation dialog, the shared submit and failure wording, the
 * refresh of the directories list (a resource view, so the connect invalidates
 * resource caches as well as authored reads), and the entered secret: the mutation
 * is `transient`, so the key or password leaves the mutation cache once the
 * connect settles. A vendor declares its connect mutation, its fields, its message
 * range (`<i18nPrefix>.button`, `.title` and `.description`, contributed to the
 * parties namespace by its manifest) and how raw values become the mutation's
 * variables; its toolbar entry `requires` `DIRECTORY_CREATE`.
 */
export function ConnectDirectoryAction<
  TDocument extends AuthoredDocument,
  TValues extends AuthoredVariables<TDocument>,
>({
  document,
  fields,
  i18nPrefix,
  parseValues,
}: ConnectDirectoryActionProps<TDocument, TValues>): React.ReactElement {
  const t = usePartiesT();
  const [connect] = useAuthoredResourceMutation(document, {
    invalidateModels: [DIRECTORY_MODEL],
    transient: true,
  });
  const dialogFields = React.useMemo(() => fields(t), [fields, t]);

  return (
    <MutationDialog
      trigger={<ActionTrigger glyph="plus">{t(`${i18nPrefix}.button`)}</ActionTrigger>}
      title={t(`${i18nPrefix}.title`)}
      description={t(`${i18nPrefix}.description`)}
      fields={dialogFields}
      submitLabel={t("directory.connect.submit")}
      submittingLabel={t("directory.connect.submitting")}
      errorFallback={t("directory.connect.error")}
      parseValues={parseValues}
      mutation={connect}
    />
  );
}
