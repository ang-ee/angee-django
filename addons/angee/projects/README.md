# Project record visibility

Task forms declare the shared [visibility field widget](../../../packages/ui/src/widgets/visibility.tsx)
beside the title. It binds `set_task_visibility` to the record's
`allowed_visibility` and revision; dirty and pending forms block the verb.

Task domains extend `visibility_blockers(value)` with SQL conditions and their
validation exceptions. Both the locked verb and optimized choice projection use
those declarations. The projection combines native narrow/widen permission
scopes with domain constraints without returning hidden domain facts.
