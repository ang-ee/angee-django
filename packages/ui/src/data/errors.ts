export function errorFromUnknown(error: unknown): Error | null {
  if (!error) return null;
  if (error instanceof Error) return error;
  const message = typeof error === "object" && "message" in error
    ? String((error as { message?: unknown }).message)
    : String(error);
  return new Error(message);
}
