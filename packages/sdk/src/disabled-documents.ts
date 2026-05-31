// Placeholder operations for paused hooks. A hook that is disabled (missing id,
// `enabled: false`) still calls urql unconditionally — Rules of Hooks — so it
// passes one of these and pauses execution. One definition each, shared by every
// hook, rather than a private copy per file.
export const DISABLED_DOCUMENTS = {
  query: "query angeeDisabled { __typename }",
  mutation: "mutation angeeDisabled { __typename }",
  subscription: "subscription angeeDisabled { __typename }",
} as const;
