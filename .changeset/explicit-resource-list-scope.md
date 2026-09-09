---
"@angee/ui": patch
---

Honor explicit resource-list scope across different resources. Nested lists use
`scope="local"` for independent sorting, filtering and selection; `scope="inherit"`
continues to reuse the ambient view state even when the resource changes.
