# IAM credential record action

Saved User records inherit IAM's issue-password action. `useIssuePasswordAction`
is exported for other IAM-owned presentations. Eligibility comes from
`can_issue_password`; the mutation is transient and the returned secret lives
only in a read-only, copyable `usePrompt` reveal. No credential is added to a
record or query cache. The existing manually entered reset action and its policy
remain unchanged.
