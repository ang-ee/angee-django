// Test-only: read the operation name from an authored document so a mocked
// `useAuthoredQuery` can route by which operation it was handed. After the
// `graphql()` migration the document is a parsed `DocumentNode`, not the source
// string, so the operation name comes from its first definition; a raw-string
// document (the pre-migration shape) is matched by substring as a fallback.

interface NamedDefinition {
  name?: { value?: string };
}

interface OperationDocument {
  definitions?: readonly NamedDefinition[];
}

export function documentName(document: unknown): string | undefined {
  if (typeof document === "string") {
    const match = document.match(/\b(?:query|mutation|subscription)\s+(\w+)/);
    return match?.[1];
  }
  const definitions = (document as OperationDocument | null)?.definitions;
  return definitions?.[0]?.name?.value;
}
