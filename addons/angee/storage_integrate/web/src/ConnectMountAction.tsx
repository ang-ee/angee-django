import {
  useAuthoredMutation,
  type AuthoredDocument,
  type AuthoredVariables,
  type DocumentVariables,
} from "@angee/refine";
import {
  Button,
  DialogForm,
  ErrorBanner,
  FieldLabel,
  FieldRoot,
  Glyph,
  Input,
  Select,
  errorMessage,
} from "@angee/ui";
import * as React from "react";

import { useStorageIntegrateT } from "./i18n";
import { MountSourceBrowser } from "./MountSourceBrowser";

type MountMode<TDocument extends AuthoredDocument> = NonNullable<
  DocumentVariables<TDocument> extends { mode: infer TMode } ? TMode : never
>;

export interface ConnectMountActionProps<TDocument extends AuthoredDocument> {
  mutationDocument: TDocument;
  backendClass: string;
  i18nPrefix: string;
  idPrefix: string;
  invalidateModel: string;
}

/** Shared dialog action for provisioning a browsable external-source Mount. */
export function ConnectMountAction<TDocument extends AuthoredDocument>({
  mutationDocument,
  backendClass,
  i18nPrefix,
  idPrefix,
  invalidateModel,
}: ConnectMountActionProps<TDocument>): React.ReactElement {
  type Mode = MountMode<TDocument>;

  const t = useStorageIntegrateT();
  const key = React.useCallback(
    (suffix: string) => `${i18nPrefix}.${suffix}`,
    [i18nPrefix],
  );
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [path, setPath] = React.useState("");
  const [mode, setMode] = React.useState<Mode>("REFERENCE" as Mode);
  const [error, setError] = React.useState<string | null>(null);
  const [connect, connectState] = useAuthoredMutation(mutationDocument, {
    invalidateModels: [invalidateModel],
  });

  React.useEffect(() => {
    if (!open) {
      setName("");
      setPath("");
      setMode("REFERENCE" as Mode);
      setError(null);
    }
  }, [open]);

  const modeOptions = React.useMemo(
    () => [
      { value: "COPY", label: t(key("modeCopy")) },
      { value: "REFERENCE", label: t(key("modeReference")) },
    ],
    [key, t],
  );
  const ready = name.trim() !== "" && path !== "";
  const footer = (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={connectState.fetching}
        onClick={() => setOpen(false)}
      >
        {t(key("cancel"))}
      </Button>
      <Button
        type="submit"
        variant="primary"
        size="sm"
        disabled={!ready || connectState.fetching}
        loading={connectState.fetching}
        loadingText={t(key("submitting"))}
      >
        {t(key("submit"))}
      </Button>
    </>
  );

  async function submit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!ready || connectState.fetching) return;
    setError(null);
    try {
      await connect({
        name: name.trim(),
        path,
        mode,
      } as AuthoredVariables<TDocument>);
      setOpen(false);
    } catch (cause) {
      setError(errorMessage(cause, t(key("error"))));
    }
  }

  return (
    <>
      <Button variant="primary" size="sm" onClick={() => setOpen(true)}>
        <Glyph decorative name="plus" />
        {t(key("button"))}
      </Button>
      <DialogForm
        open={open}
        onOpenChange={setOpen}
        title={t(key("title"))}
        description={t(key("description"))}
        size="lg"
        footer={footer}
        onSubmit={(event) => void submit(event)}
      >
        <FieldRoot>
          <FieldLabel htmlFor={`${idPrefix}-name`} required>
            {t(key("name"))}
          </FieldLabel>
          <Input
            id={`${idPrefix}-name`}
            placeholder={t(key("namePlaceholder"))}
            value={name}
            required
            disabled={connectState.fetching}
            onChange={(event) => setName(event.currentTarget.value)}
          />
        </FieldRoot>

        <MountSourceBrowser
          backendClass={backendClass}
          open={open}
          value={path}
          onChange={setPath}
        />

        <FieldRoot>
          <FieldLabel htmlFor={`${idPrefix}-mode`} required>
            {t(key("mode"))}
          </FieldLabel>
          <Select
            id={`${idPrefix}-mode`}
            aria-label={t(key("mode"))}
            options={modeOptions}
            value={mode as string}
            disabled={connectState.fetching}
            onValueChange={(value) => setMode(mountModeValue<Mode>(value))}
          />
        </FieldRoot>
        <ErrorBanner description={error} />
      </DialogForm>
    </>
  );
}

function mountModeValue<TMode>(value: string): TMode {
  if (value === "COPY" || value === "REFERENCE") return value as TMode;
  throw new Error("A mount mode is required.");
}
