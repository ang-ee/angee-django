/** What the address answers to a JSON GET: the version the form serves now. */
export interface FormDescription {
  version: number;
  /** The checked subset of a SurveyJS survey definition. */
  definition: object;
  languages: string[];
  default_language: string;
  /** The language this address opens the form in; empty leaves it to the visitor. */
  language: string;
  /** The theme the page wears; empty follows the deployment's default. */
  theme: string;
  /** The field a person never fills in; empty when the deployment sets none. */
  honeypot_field: string;
}

/** What the address answers when it refuses a submission. */
export interface Refusal {
  message?: string;
  /** Refusals by question name, or by the part of the submission they are about. */
  fields?: Record<string, string[]>;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isStrings = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === "string");

/**
 * Read the description of a form from what the address answered. An answer of
 * another shape is not a form the page can show, and is refused here rather
 * than wherever the page would first trip over it.
 */
export function readDescription(value: unknown): FormDescription {
  if (isRecord(value)) {
    const { version, definition, languages, default_language, language, theme, honeypot_field } = value;
    if (
      typeof version === "number" && isRecord(definition) && isStrings(languages)
      && typeof default_language === "string" && languages.includes(default_language)
      && typeof language === "string" && typeof theme === "string" && typeof honeypot_field === "string"
    ) return { version, definition, languages, default_language, language, theme, honeypot_field };
  }
  throw new TypeError("The address did not describe a form.");
}

/** Read a refusal from what the address answered, keeping only the parts that have the shape the page shows. */
export function readRefusal(value: unknown): Refusal {
  if (!isRecord(value)) return {};
  const fields = isRecord(value.fields)
    ? Object.fromEntries(Object.entries(value.fields).filter((entry): entry is [string, string[]] => isStrings(entry[1])))
    : undefined;
  return {
    ...(typeof value.message === "string" && value.message ? { message: value.message } : {}),
    ...(fields && Object.keys(fields).length > 0 ? { fields } : {}),
  };
}

/**
 * A new id for one submission: 128 random bits in hex, within the server's rule
 * for a submission id. `crypto.getRandomValues` exists on a plain http address
 * too, where `crypto.randomUUID` does not.
 */
export function newSubmissionId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
