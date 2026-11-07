---
"@angee/refine": patch
"@angee/ui": patch
"@angee/app": patch
---

Native query lifecycles survive development remounts, controlled root owners
hear an authoritative invalidation, and route checks read the session through
`ensureQueryData` instead of re-sending identity reads.
