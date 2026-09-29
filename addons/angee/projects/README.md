# Project record audience

`TaskVisibility` binds the shared inline visibility widget to
`set_task_visibility`, using the record's `allowed_visibility` and revision.
Task title fields compose it through `labelAccessory`; dirty and pending forms
block the independent audience action.

Task domains extend `visibility_blockers(value)` with SQL conditions and their
validation exceptions. Both the locked verb and optimized choice projection use
those declarations. The projection combines native narrow/widen permission
scopes with domain constraints without returning hidden domain facts.
