import { useCallback, type ReactNode } from "react";

import { usePrompt, type PromptCopy } from "./ModalsHost";

/** One secret shown once: a password, an API key, a rotated token. */
export interface RevealedSecret {
  name: string;
  label: ReactNode;
  value: string;
}

export interface SecretRevealOptions {
  title: ReactNode;
  /** The warning that the values will not be shown again. */
  body?: ReactNode;
  secrets: readonly RevealedSecret[];
  /** One extra clipboard control for a value composed from the secrets (sign-in details). */
  copy?: PromptCopy;
}

/**
 * The one-time secret reveal ceremony: a read-only prompt whose fields are each
 * copyable, plus an optional combined copy control. Nothing reaches a cache or
 * the toast queue; the dialog is the only place the values appear. Addons that
 * issue passwords, reveal credentials or rotate webhook secrets compose this
 * instead of spelling the prompt themselves.
 * @owner ModalsHost
 */
export function useSecretReveal(): (options: SecretRevealOptions) => Promise<void> {
  const prompt = usePrompt();
  return useCallback(async ({ title, body, secrets, copy }) => {
    await prompt({
      title,
      body,
      copy,
      fields: secrets.map((secret) => ({
        name: secret.name, label: secret.label, defaultValue: secret.value, readOnly: true, copyable: true,
      })),
    });
  }, [prompt]);
}
