# GraphQL pitfalls

Moved from the backend guide. The contracts stayed there:
[GraphQL actor and write contracts](../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts) and
[Schema lifecycle and change publication](../../../../docs/backend/guidelines.md#schema-lifecycle-and-change-publication).

- **GraphQL authorization tests include a non-admin reader.** Admin-only tests
  neither pin deny-hard-fail behavior nor expose a leaked `sudo()` scope.
- **`hasura_model_resource` create `full_clean`s the input, so model + input defaults must agree.**
  The [create contract](../../../../docs/backend/guidelines.md#graphql-actor-and-write-contracts)
  owns the rule: validation before persistence, defaults in `clean()`/`save()`,
  factory invariants on the queryset's cooperative `insert()`, preparation
  through the write backend's manager-factory seam. Do not depend on the
  prepared instance's identity surviving: the locked version persists through
  `manager.create(**kwargs)`. Two input traps follow. (1) A `JSONField(default=dict)`
  (or `default=list`) needs `blank=True`: Django counts `{}`/`[]` as blank, so a
  `blank=False` container default fails `full_clean` ("cannot be blank") on every
  create. (2) An optional create-input field over a **non-null** column that
  defaults to `None` is submitted as an explicit null that overwrites the model
  default (e.g. `status`/`config`), and `full_clean` then rejects the null; it
  defaults to `strawberry.UNSET`. Mirror this for any new
  `hasura_model_resource` input.
- **A `strawberry_django.field(only=[...])` hint must list every column the resolver dereferences.**
  Include columns read by shared properties the resolver delegates to; otherwise
  selecting that field alone can defer-load the missing column per row.
- **Type extensions retain native optimizer hints.** The
  [schema lifecycle contract](../../../../docs/backend/guidelines.md#schema-lifecycle-and-change-publication)
  owns the rule (declare hints on the contributed field). Prove it: exercise
  narrow list selections at multiple row counts, including named schemas with
  different extensions.
- **Intersect write-only fields out of the read/return selection** — a field
  absent from the SDL read type (e.g. `password`) makes the detail query invalid
  and the form loads blank if it is selected.
