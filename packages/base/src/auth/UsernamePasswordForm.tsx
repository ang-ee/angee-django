import { useId, useState, type FormEvent, type ReactNode } from "react";
import { useLoginWithPassword } from "@angee/sdk";

import { Button } from "../ui/button";
import { FieldControl, FieldLabel, FieldRoot } from "../ui/field";

export interface UsernamePasswordFormProps {
  /** Called after a successful sign-in; the page handles the redirect. */
  onSuccess?: () => void;
  /** Optional help action rendered directly under the submit button. */
  passwordHelp?: ReactNode;
}

/**
 * Username + password sign-in form. Submits through
 * `useLoginWithPassword().login`; on `{ ok: true }` it calls `onSuccess`, and on
 * a rejected login or a thrown error it shows an inline message. Inputs are
 * disabled while the request is in flight.
 */
export function UsernamePasswordForm({
  onSuccess,
  passwordHelp,
}: UsernamePasswordFormProps): ReactNode {
  const { login, fetching } = useLoginWithPassword();
  const usernameId = useId();
  const passwordId = useId();
  const errorId = useId();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    try {
      const result = await login({ username, password });
      if (result.ok) {
        onSuccess?.();
        return;
      }
      setError("Incorrect username or password.");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Something went wrong.",
      );
    }
  }

  const hasError = error !== null;

  return (
    <form className="flex flex-col gap-4" onSubmit={onSubmit}>
      <FieldRoot invalid={hasError} size="lg">
        <FieldLabel htmlFor={usernameId} required>
          Username
        </FieldLabel>
        <FieldControl
          id={usernameId}
          name="username"
          type="text"
          autoComplete="username"
          className="!h-11 bg-canvas"
          required
          disabled={fetching}
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          aria-label="Username"
          aria-invalid={hasError}
          aria-describedby={hasError ? errorId : undefined}
        />
      </FieldRoot>

      <FieldRoot invalid={hasError} size="lg">
        <FieldLabel htmlFor={passwordId} required>
          Password
        </FieldLabel>
        <FieldControl
          id={passwordId}
          name="password"
          type="password"
          autoComplete="current-password"
          className="!h-11 bg-canvas"
          required
          disabled={fetching}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          aria-label="Password"
          aria-invalid={hasError}
          aria-describedby={hasError ? errorId : undefined}
        />
      </FieldRoot>

      {hasError ? (
        <p
          id={errorId}
          role="alert"
          className="rounded-md border border-danger bg-danger-soft px-3 py-2 text-13 text-danger-text"
        >
          {error}
        </p>
      ) : null}

      <Button
        type="submit"
        variant="primary"
        size="lg"
        loading={fetching}
        className="mt-2 !h-11 w-full justify-center"
      >
        {fetching ? "Signing in…" : "Sign in"}
      </Button>

      {passwordHelp ? (
        <div className="-mt-1 flex justify-center text-sm">
          {passwordHelp}
        </div>
      ) : null}
    </form>
  );
}
