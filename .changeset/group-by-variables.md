---
"@angee/refine": minor
---

`groupByVariables` returns a new named type, `GroupByVariables` (`group_by`,
`where`, `having`, `order_by`, `limit`, `offset`, `with_rows`, `rows_limit` and
`rows_order_by`), instead of `Record<string, unknown>`; the values it builds are
unchanged. A page that hands them to a generated groups document narrows
`group_by` to its resource's own type at that one member instead of casting the
whole object. Code that read or added other keys on the result as a plain record
no longer type-checks.
